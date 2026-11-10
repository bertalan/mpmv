"""TAX.5 / D-28: the VIES consultation log an order can use (needs human review).

The consultation number (`requestIdentifier`) is returned only when the requester's own VAT
number is sent, so for an order the requester is the VENDOR. The row keeps date, time, number
and requester; a cached answer is reused only when it was asked for the same requester and
carries a number. Fail closed stays. VIES is reached through fakes of our `ViesClient` only.
"""

import pytest
from django.utils import timezone

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
