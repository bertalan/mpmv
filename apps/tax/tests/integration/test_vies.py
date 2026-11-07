"""TAX.5: the VIES check with its cache, timeout and fail-closed policy (spec 05 §5.2).

A VAT number counts as verified (reverse charge possible) only when VIES confirmed it within
the cache period. VIES down, too slow, an invalid number or a prefix that is not the country's:
not verified, with the reason. VIES is reached only through fakes of our `ViesClient`.
"""

import datetime as dt
from typing import Any

import pytest
from django.utils import timezone
from pytest_django.fixtures import SettingsWrapper

from apps.core.audit import AuditContext
from apps.core.tests.factories import PlatformOperatorFactory
from apps.tax import engine, services, tasks
from apps.tax.engine import ViesAnswer, ViesQuery
from apps.tax.models import VatValidation
from apps.tax.tests.factories import VatValidationFactory
from apps.tax.tests.fakes import (
    DOWN,
    INVALID,
    TIMED_OUT,
    BrokenViesClient,
    DownViesClient,
    FakeViesClient,
)
from apps.tax.vies import HttpViesClient, ViesVatNumberValidator, get_vies_client
from apps.vendors import services as vendor_services
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

VALID = ViesAnswer(
    result=engine.VAT_VALID,
    name="Example Customer GmbH",
    address="Example Strasse 1, 10115 Berlin",
    request_id="WAPIAAAAZ1x2y3",
)


def _ago(**delta: Any) -> dt.datetime:
    return timezone.now() - dt.timedelta(**delta)


# --- Answers from VIES


def test_a_valid_number_is_verified_and_the_answer_is_stored() -> None:
    client = FakeViesClient(default=VALID)
    check = services.verify_vat_number("DE", "DE 123 456 789", client=client)
    assert check.verified is True
    assert (check.result, check.reason) == (engine.VAT_VALID, engine.VAT_REASON_VALID)
    assert check.from_cache is False
    assert client.calls == [ViesQuery("DE", "DE", "123456789")]
    row = VatValidation.objects.get()
    assert row == check.validation
    assert (row.country, row.vat_number, row.result) == ("DE", "123456789", engine.VAT_VALID)
    assert (row.name, row.request_id) == ("Example Customer GmbH", "WAPIAAAAZ1x2y3")
    assert check.request_id == "WAPIAAAAZ1x2y3"
    assert check.checked_at == row.checked_at


def test_an_invalid_number_is_not_verified() -> None:
    check = services.verify_vat_number("DE", "123456789", client=FakeViesClient(default=INVALID))
    assert check.verified is False
    assert check.reason == engine.VAT_REASON_INVALID
    assert "VAT is charged" in check.message
    assert VatValidation.objects.get().result == engine.VAT_INVALID


def test_vies_down_fails_closed_with_the_reason() -> None:
    check = services.verify_vat_number("DE", "123456789", client=FakeViesClient(default=DOWN))
    assert check.verified is False
    assert (check.result, check.reason) == (engine.VAT_UNAVAILABLE, engine.VAT_REASON_UNAVAILABLE)
    assert check.detail == "MS_UNAVAILABLE"
    assert "not verified" in check.message
    assert VatValidation.objects.get().result == engine.VAT_UNAVAILABLE


def test_a_vies_timeout_fails_closed_with_the_reason() -> None:
    check = services.verify_vat_number("DE", "123456789", client=FakeViesClient(default=TIMED_OUT))
    assert check.verified is False
    assert check.reason == engine.VAT_REASON_TIMEOUT


def test_a_client_that_raises_fails_closed() -> None:
    check = services.verify_vat_number("DE", "123456789", client=BrokenViesClient())
    assert check.verified is False
    assert check.reason == engine.VAT_REASON_UNAVAILABLE
    assert VatValidation.objects.get().result == engine.VAT_UNAVAILABLE


def test_a_greek_number_is_asked_as_el_and_stored_as_gr() -> None:
    client = FakeViesClient(default=VALID)
    check = services.verify_vat_number("GR", "EL123456789", client=client)
    assert client.calls == [ViesQuery("GR", "EL", "123456789")]
    assert (check.country, check.vat_number) == ("GR", "123456789")
    assert VatValidation.objects.get().country == "GR"


def test_long_vies_texts_are_shortened_to_fit() -> None:
    answer = ViesAnswer(result=engine.VAT_VALID, name="N" * 300, request_id="R" * 100)
    check = services.verify_vat_number("DE", "123456789", client=FakeViesClient(default=answer))
    assert check.validation is not None
    assert (len(check.validation.name), len(check.validation.request_id)) == (200, 64)


# --- Numbers VIES is never asked about (no row: every row is a VIES answer)


@pytest.mark.parametrize(
    ("country", "raw", "reason"),
    [
        ("DE", "INVALID12345", engine.VAT_REASON_PREFIX_MISMATCH),  # spec 05 T2
        ("DE", "ATU12345678", engine.VAT_REASON_PREFIX_MISMATCH),
        ("DE", "12#4", engine.VAT_REASON_MALFORMED),
        ("GB", "GB123456789", engine.VAT_REASON_NOT_IN_VIES),
    ],
)
def test_a_number_vies_cannot_confirm_is_not_verified_without_asking(
    country: str, raw: str, reason: str
) -> None:
    client = FakeViesClient(default=VALID)
    check = services.verify_vat_number(country, raw, client=client)
    assert check.verified is False
    assert (check.result, check.reason) == (None, reason)
    assert check.validation is None
    assert client.calls == []
    assert not VatValidation.objects.exists()


def test_spec_t2_invalid_number_with_vies_down_is_not_verified() -> None:
    check = services.verify_vat_number("DE", "INVALID12345", client=DownViesClient())
    assert check.verified is False


# --- Cache


def test_a_recent_answer_is_reused_without_asking_vies(settings: SettingsWrapper) -> None:
    settings.VIES_CACHE_SECONDS = 3600
    cached = VatValidationFactory.create(
        country="DE", vat_number="123456789", checked_at=_ago(minutes=30)
    )
    client = FakeViesClient(default=INVALID)
    check = services.verify_vat_number("DE", "DE123456789", client=client)
    assert check.verified is True
    assert check.from_cache is True
    assert check.validation == cached
    assert client.calls == []


def test_an_answer_older_than_the_cache_period_is_asked_again(settings: SettingsWrapper) -> None:
    settings.VIES_CACHE_SECONDS = 3600
    VatValidationFactory.create(country="DE", vat_number="123456789", checked_at=_ago(hours=2))
    client = FakeViesClient(default=INVALID)
    check = services.verify_vat_number("DE", "123456789", client=client)
    assert check.verified is False
    assert check.from_cache is False
    assert len(client.calls) == 1
    assert VatValidation.objects.count() == 2


def test_an_old_valid_answer_does_not_survive_vies_being_down(settings: SettingsWrapper) -> None:
    settings.VIES_CACHE_SECONDS = 3600
    VatValidationFactory.create(country="DE", vat_number="123456789", checked_at=_ago(days=2))
    check = services.verify_vat_number("DE", "123456789", client=DownViesClient())
    assert check.verified is False
    assert check.reason == engine.VAT_REASON_UNAVAILABLE


def test_vies_down_is_remembered_only_briefly(settings: SettingsWrapper) -> None:
    settings.VIES_UNAVAILABLE_CACHE_SECONDS = 300
    VatValidationFactory.create(
        country="DE",
        vat_number="123456789",
        result=engine.VAT_UNAVAILABLE,
        checked_at=_ago(minutes=1),
    )
    client = FakeViesClient(default=VALID)
    check = services.verify_vat_number("DE", "123456789", client=client)
    assert (check.verified, check.from_cache) == (False, True)
    assert check.reason == engine.VAT_REASON_UNAVAILABLE
    assert client.calls == []

    VatValidation.objects.all().delete()
    VatValidationFactory.create(
        country="DE",
        vat_number="123456789",
        result=engine.VAT_UNAVAILABLE,
        checked_at=_ago(minutes=10),
    )
    assert services.verify_vat_number("DE", "123456789", client=client).verified is True
    assert len(client.calls) == 1


def test_a_recent_confirmation_wins_over_a_later_outage(settings: SettingsWrapper) -> None:
    settings.VIES_CACHE_SECONDS = 3600
    VatValidationFactory.create(country="DE", vat_number="123456789", checked_at=_ago(minutes=40))
    VatValidationFactory.create(
        country="DE",
        vat_number="123456789",
        result=engine.VAT_UNAVAILABLE,
        checked_at=_ago(minutes=1),
    )
    check = services.verify_vat_number("DE", "123456789", client=DownViesClient())
    assert (check.verified, check.from_cache) == (True, True)


def test_the_newest_definitive_answer_counts(settings: SettingsWrapper) -> None:
    settings.VIES_CACHE_SECONDS = 3600
    VatValidationFactory.create(country="DE", vat_number="123456789", checked_at=_ago(minutes=40))
    VatValidationFactory.create(
        country="DE",
        vat_number="123456789",
        result=engine.VAT_INVALID,
        checked_at=_ago(minutes=5),
    )
    check = services.verify_vat_number("DE", "123456789", client=FakeViesClient(default=VALID))
    assert (check.verified, check.reason) == (False, engine.VAT_REASON_INVALID)


def test_refresh_skips_the_cache() -> None:
    VatValidationFactory.create(country="DE", vat_number="123456789", checked_at=_ago(minutes=1))
    client = FakeViesClient(default=INVALID)
    check = services.verify_vat_number("DE", "123456789", client=client, refresh=True)
    assert (check.verified, check.from_cache) == (False, False)
    assert len(client.calls) == 1


def test_the_snapshot_holds_what_an_order_keeps() -> None:
    check = services.verify_vat_number("DE", "123456789", client=FakeViesClient(default=VALID))
    snapshot = check.snapshot()
    assert snapshot == {
        "country": "DE",
        "vat_number": "123456789",
        "verified": True,
        "result": engine.VAT_VALID,
        "reason": engine.VAT_REASON_VALID,
        "detail": "",
        "request_id": "WAPIAAAAZ1x2y3",
        "checked_at": check.checked_at.isoformat() if check.checked_at else None,
        "validation": str(check.validation.public_id) if check.validation else None,
    }


# --- Configured client


def test_the_configured_client_is_used_by_default(settings: SettingsWrapper) -> None:
    settings.VIES_CLIENT = "apps.tax.tests.fakes.DownViesClient"
    assert isinstance(get_vies_client(), DownViesClient)
    assert services.verify_vat_number("DE", "123456789").verified is False


def test_the_http_client_asks_as_the_platform_operator(settings: SettingsWrapper) -> None:
    settings.VIES_TIMEOUT_SECONDS = 3.5
    PlatformOperatorFactory.create(
        country="AT", vat_number="ATU98765432", valid_from=_ago(days=1), valid_to=None
    )
    client = HttpViesClient.from_settings()
    assert client.requester == ViesQuery("AT", "AT", "U98765432")
    assert client.timeout == 3.5
    assert client.url == settings.VIES_API_URL


def test_the_http_client_asks_anonymously_without_a_platform_operator() -> None:
    assert HttpViesClient.from_settings().requester is None


# --- Vendors' VatNumberValidator (VEN.12) through the cached check


def test_the_vendor_vat_check_goes_through_the_cached_vies_check() -> None:
    vendor = VendorFactory.create(country="DE", vat_number="DE123456789")
    client = FakeViesClient(default=VALID)
    ctx = AuditContext(actor_label="system:test", source="test")
    vendor_services.check_vat_number(vendor, ViesVatNumberValidator(client=client), ctx=ctx)
    vendor.refresh_from_db()
    assert vendor.vies_status == engine.VAT_VALID
    assert VatValidation.objects.get().request_id == "WAPIAAAAZ1x2y3"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("123456789", engine.VAT_UNAVAILABLE), ("12#4", engine.VAT_INVALID)],
)
def test_the_vendor_validator_answers_a_vies_result(raw: str, expected: str) -> None:
    validator = ViesVatNumberValidator(client=DownViesClient())
    assert validator.check("DE", raw) == expected


def test_the_vendor_validator_reads_the_greek_prefix() -> None:
    client = FakeViesClient(default=VALID)
    assert ViesVatNumberValidator(client=client).check("EL", "123456789") == engine.VAT_VALID
    assert client.calls == [ViesQuery("GR", "EL", "123456789")]


# --- Background re-check: retries with backoff only here


def test_the_background_recheck_raises_while_vies_is_down(settings: SettingsWrapper) -> None:
    settings.VIES_CLIENT = "apps.tax.tests.fakes.DownViesClient"
    with pytest.raises(services.ViesUnavailable):
        tasks.recheck_vat_number.func(country="DE", vat_number="123456789")
    assert VatValidation.objects.get().result == engine.VAT_UNAVAILABLE


def test_the_background_recheck_stores_a_fresh_answer(settings: SettingsWrapper) -> None:
    settings.VIES_CLIENT = "apps.tax.tests.fakes.ValidViesClient"
    VatValidationFactory.create(
        country="DE", vat_number="123456789", result=engine.VAT_INVALID, checked_at=_ago(minutes=1)
    )
    tasks.recheck_vat_number.func(country="DE", vat_number="123456789")
    assert VatValidation.objects.order_by("-checked_at").first().result == engine.VAT_VALID  # type: ignore[union-attr]


def test_the_background_recheck_ignores_a_number_vies_cannot_check(
    settings: SettingsWrapper,
) -> None:
    settings.VIES_CLIENT = "apps.tax.tests.fakes.DownViesClient"
    tasks.recheck_vat_number.func(country="GB", vat_number="123456789")
    assert not VatValidation.objects.exists()


def test_the_background_recheck_retries_with_growing_delays() -> None:
    delays = [tasks.VIES_RETRY.delay_after(attempt) for attempt in range(3)]
    assert delays[0] is not None
    assert delays == sorted(delays)
    assert delays[0] < delays[2]  # type: ignore[operator]
