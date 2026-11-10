"""TAX.5 / D-28: the VIES consultation log an order can use (needs human review).

The consultation number (`requestIdentifier`) is returned only when the requester's own VAT
number is sent, so for an order the requester is the VENDOR. The row keeps date, time, number
and requester; a cached answer is reused only when it was asked for the same requester and
carries a number. Fail closed stays. VIES is reached through fakes of our `ViesClient` only.
"""

import datetime as dt
from typing import Any

import pytest
from django.contrib import admin
from django.db import connection, transaction
from django.db.utils import InternalError, ProgrammingError
from django.utils import timezone

from apps.core.tests.factories import PlatformOperatorFactory
from apps.tax import engine, services
from apps.tax.engine import ViesAnswer, ViesQuery
from apps.tax.models import VatValidation
from apps.tax.tests.factories import VatValidationFactory
from apps.tax.tests.fakes import DownViesClient, FakeViesClient

pytestmark = pytest.mark.django_db

VENDOR = engine.vies_query("IT", "IT 01234567897")
OTHER_VENDOR = engine.vies_query("FR", "FR12345678901")
VALID = ViesAnswer(result=engine.VAT_VALID, request_id="WAPIVENDOR0001")


class RequesterAwareClient(FakeViesClient):
    """Gives a consultation number only when asked with a requester, like VIES."""

    def __init__(self) -> None:
        super().__init__(default=VALID)

    def check(self, query: ViesQuery, *, requester: ViesQuery | None = None) -> ViesAnswer:
        self.calls.append(query)
        self.requesters.append(requester)
        return VALID if requester is not None else ViesAnswer(result=engine.VAT_VALID)


def test_the_vendor_is_sent_as_requester_and_the_consultation_log_is_stored() -> None:
    client = RequesterAwareClient()
    check = services.verify_vat_number("DE", "DE123456789", client=client, requester=VENDOR)
    assert client.requesters == [VENDOR]
    row = VatValidation.objects.get()
    assert row.request_id == "WAPIVENDOR0001"
    assert row.requester == "IT01234567897"
    assert row.checked_at <= timezone.now()
    snapshot = check.snapshot()
    assert snapshot["request_id"] == "WAPIVENDOR0001"
    assert snapshot["requester"] == "IT01234567897"
    assert snapshot["checked_at"] == row.checked_at.isoformat()


def test_without_a_requester_the_client_is_called_as_before() -> None:
    client = FakeViesClient(default=VALID)
    check = services.verify_vat_number("DE", "DE123456789", client=client)
    assert check.snapshot()["requester"] == ""
    assert VatValidation.objects.get().requester == ""


def test_an_answer_without_a_consultation_number_is_not_reused_for_a_vendor() -> None:
    VatValidationFactory.create(country="DE", vat_number="123456789", request_id="")
    client = RequesterAwareClient()
    check = services.verify_vat_number("DE", "DE123456789", client=client, requester=VENDOR)
    assert check.from_cache is False
    assert len(client.calls) == 1
    assert check.request_id == "WAPIVENDOR0001"


def test_an_answer_asked_for_another_vendor_is_not_reused() -> None:
    VatValidationFactory.create(
        country="DE", vat_number="123456789", request_id="WAPIOTHER", requester="FR12345678901"
    )
    client = RequesterAwareClient()
    check = services.verify_vat_number("DE", "DE123456789", client=client, requester=VENDOR)
    assert check.from_cache is False
    assert check.validation is not None
    assert check.validation.requester == "IT01234567897"


def test_an_answer_for_the_same_vendor_with_a_number_is_reused() -> None:
    row = VatValidationFactory.create(
        country="DE", vat_number="123456789", request_id="WAPISAME", requester="IT01234567897"
    )
    client = RequesterAwareClient()
    check = services.verify_vat_number("DE", "DE123456789", client=client, requester=VENDOR)
    assert check.from_cache is True
    assert check.validation == row
    assert client.calls == []


def test_vies_down_still_fails_closed_with_a_requester() -> None:
    check = services.verify_vat_number(
        "DE", "DE123456789", client=DownViesClient(), requester=VENDOR
    )
    assert check.verified is False
    assert check.result == engine.VAT_UNAVAILABLE
    assert check.request_id == ""


def test_a_recent_unavailable_answer_is_reused_so_an_outage_does_not_stall_checkout() -> None:
    VatValidationFactory.create(
        country="DE",
        vat_number="123456789",
        result=engine.VAT_UNAVAILABLE,
        request_id="",
    )
    client = RequesterAwareClient()
    check = services.verify_vat_number("DE", "DE123456789", client=client, requester=VENDOR)
    assert check.from_cache is True
    assert check.verified is False


def test_the_default_requester_is_the_operators_and_is_recorded_as_sent() -> None:
    """Review 1: the row never shows an empty requester for an operator-issued number."""
    operator = PlatformOperatorFactory.create(country="AT", vat_number="ATU12345678")
    client = RequesterAwareClient()
    check = services.verify_vat_number("DE", "DE123456789", client=client)
    assert [r.full_number for r in client.requesters if r] == ["ATU12345678"]
    row = check.validation
    assert row is not None
    assert (row.requester, row.requester_kind) == ("ATU12345678", "operator")
    assert operator.vat_number == "ATU12345678"


def test_a_vendor_requester_is_recorded_as_vendor_proof() -> None:
    check = services.verify_vat_number(
        "DE", "DE123456789", client=RequesterAwareClient(), requester=VENDOR
    )
    assert check.snapshot()["requester_kind"] == "vendor"


def test_no_operator_and_no_vendor_records_an_anonymous_check() -> None:
    check = services.verify_vat_number("DE", "DE123456789", client=RequesterAwareClient())
    assert check.snapshot()["requester_kind"] == ""
    assert check.snapshot()["requester"] == ""


def test_a_vendor_without_a_valid_vat_number_has_no_vendor_proof() -> None:
    """Review 2: no silent fallback; the caller sees None and the log says operator proof."""
    assert services.vendor_requester("IT", "") is None
    assert services.vendor_requester("IT", "not a vat number") is None
    assert services.vendor_requester("IT", "IT01234567897") == VENDOR
    PlatformOperatorFactory.create(country="AT", vat_number="ATU12345678")
    check = services.verify_vat_number(
        "DE",
        "DE123456789",
        client=RequesterAwareClient(),
        requester=services.vendor_requester("IT", ""),
    )
    assert check.snapshot()["requester_kind"] == "operator"  # i.e. "no vendor proof"


def test_the_vies_request_date_is_stored_next_to_the_local_time() -> None:
    class Dated(RequesterAwareClient):
        def check(self, query: ViesQuery, *, requester: ViesQuery | None = None) -> ViesAnswer:
            return ViesAnswer(
                result=engine.VAT_VALID,
                request_id="WAPIDATE",
                request_date="2026-11-10T12:30:45.000+01:00",
            )

    row = services.verify_vat_number(
        "DE", "DE123456789", client=Dated(), requester=VENDOR
    ).validation
    assert row is not None
    assert row.vies_request_date == dt.datetime(2026, 11, 10, 11, 30, 45, tzinfo=dt.UTC)


def test_an_invalid_requester_has_its_own_detail_a_warning_and_a_long_negative_cache(
    caplog: pytest.LogCaptureFixture, settings: Any
) -> None:
    class Rejecting(RequesterAwareClient):
        def check(self, query: ViesQuery, *, requester: ViesQuery | None = None) -> ViesAnswer:
            self.calls.append(query)
            return ViesAnswer(result=engine.VAT_UNAVAILABLE, detail=engine.DETAIL_INVALID_REQUESTER)

    client = Rejecting()
    with caplog.at_level("WARNING"):
        check = services.verify_vat_number("DE", "DE123456789", client=client, requester=VENDOR)
    assert check.verified is False
    assert check.detail == engine.DETAIL_INVALID_REQUESTER
    assert "IT01234567897" in caplog.text
    # an hour later (past the 5 minute outage cache) the same vendor is still not asked again
    VatValidation.objects.filter().count()
    row = check.validation
    assert row is not None
    assert row.detail == engine.DETAIL_INVALID_REQUESTER
    again = services.verify_vat_number("DE", "DE123456789", client=client, requester=VENDOR)
    assert again.from_cache is True
    assert len(client.calls) == 1


def test_vies_requester_error_code_is_read_as_invalid_requester() -> None:
    answer = engine.read_vies_answer(
        engine.vies_query("DE", "DE123456789"),
        {"errorWrappers": [{"error": "INVALID_REQUESTER_INFO"}]},
    )
    assert (answer.result, answer.detail) == (
        engine.VAT_UNAVAILABLE,
        engine.DETAIL_INVALID_REQUESTER,
    )


def test_an_overlong_requester_does_not_make_the_check_raise() -> None:
    long = ViesQuery("IT", "IT", "1" * 60)
    check = services.verify_vat_number(
        "DE", "DE123456789", client=RequesterAwareClient(), requester=long
    )
    assert check.validation is not None


def test_the_log_is_append_only_in_the_database() -> None:
    row = VatValidationFactory.create()
    for sql in ("UPDATE tax_vatvalidation SET name = 'x'", "DELETE FROM tax_vatvalidation"):
        with (
            pytest.raises((InternalError, ProgrammingError)),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(sql)
    assert VatValidation.objects.filter(pk=row.pk).exists()
    with pytest.raises(PermissionError):
        VatValidation.objects.filter(pk=row.pk).first().save()  # type: ignore[union-attr]
    assert VatValidation.objects.filter(pk=row.pk).exists()


def test_the_admin_shows_the_requester_read_only() -> None:
    model_admin = admin.site._registry[VatValidation]
    for name in ("requester", "requester_kind", "vies_request_date"):
        assert name in model_admin.readonly_fields
    assert "requester" in model_admin.list_display
