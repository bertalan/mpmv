"""TAX.19: Northern Ireland (`XI`) VAT numbers are checked in VIES, stored and verified.

`XI` is the VAT prefix of Northern Ireland, not an ISO country: it is a `VatValidation` country
only. A delivery to Northern Ireland keeps GB as its address country, with a BT postcode
(TAX.18). VIES is reached only through fakes of our `ViesClient`.
"""

import datetime as dt
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.tests.factories import PlatformOperatorFactory, utc
from apps.tax import engine, services
from apps.tax.engine import ViesAnswer, ViesQuery
from apps.tax.models import VatValidation
from apps.tax.tests.factories import TaxRateFactory, VatValidationFactory, VendorTaxProfileFactory
from apps.tax.tests.fakes import BrokenViesClient, DownViesClient, FakeViesClient
from apps.tax.vies import ViesClient
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

VALID = ViesAnswer(
    result=engine.VAT_VALID,
    name="Example Customer Ltd",
    address="1 Example Street, Belfast",
    request_id="WAPIXI000001",
)
XI_NUMBER = "XI 123 4567 89"
NI_POSTCODE = "BT1 1AA"


@pytest.fixture
def vendor_oss() -> Vendor:
    PlatformOperatorFactory.create(open_ended=True, valid_from=utc(2026, 1, 1), country="IT")
    vendor = VendorFactory.create(country="IT", vat_number="IT01234567897")
    VendorTaxProfileFactory.create(vendor=vendor, oss=True, valid_from=utc(2026, 1, 1))
    for country, value in (("IT", "22"), ("GB", "20")):  # the UK rate falls back to GB
        TaxRateFactory.create(
            country=country,
            category="standard",
            rate=Decimal(value),
            valid_from=utc(2026, 1, 1),
            valid_to=None,
        )
    return vendor


# --- Stored and verified


def test_a_vies_valid_xi_number_is_stored_and_verified() -> None:
    client = FakeViesClient(default=VALID)
    check = services.verify_vat_number("xi", XI_NUMBER, client=client)
    assert check.verified is True
    assert (check.country, check.vat_number) == ("XI", "123456789")
    assert client.calls == [ViesQuery("XI", "XI", "123456789")]
    row = VatValidation.objects.get()
    assert (row.country, row.vat_number, row.result) == ("XI", "123456789", engine.VAT_VALID)
    assert row.request_id == "WAPIXI000001"
    assert services.latest_vat_validation("XI", "XI123456789") == row


def test_a_recent_xi_answer_is_reused_from_the_cache() -> None:
    client = FakeViesClient(default=VALID)
    services.verify_vat_number("XI", XI_NUMBER, client=client)
    again = services.verify_vat_number("XI", "XI123456789", client=client)
    assert again.verified is True
    assert again.from_cache is True
    assert len(client.calls) == 1


def test_an_order_check_of_an_xi_number_is_sent_as_the_vendor(vendor_oss: Vendor) -> None:
    client = FakeViesClient(default=VALID)
    check = services.verify_vat_number_for_order("XI", XI_NUMBER, vendor=vendor_oss, client=client)
    assert check.verified is True
    assert client.requesters == [ViesQuery("IT", "IT", "01234567897")]
    assert check.snapshot()["country"] == "XI"


@pytest.mark.parametrize("client", [DownViesClient(), BrokenViesClient()])
def test_an_xi_check_fails_closed_and_never_raises(client: ViesClient) -> None:
    check = services.verify_vat_number("XI", XI_NUMBER, client=client)
    assert check.verified is False
    assert check.result == engine.VAT_UNAVAILABLE


@pytest.mark.parametrize(
    ("country", "raw", "reason"),
    [
        ("XI", "GB123456789", engine.VAT_REASON_PREFIX_MISMATCH),
        ("GB", "GB123456789", engine.VAT_REASON_NOT_IN_VIES),
        ("XI", "XI", engine.VAT_REASON_MALFORMED),
    ],
)
def test_a_gb_only_or_malformed_number_is_not_asked_and_never_raises(
    country: str, raw: str, reason: str
) -> None:
    client = FakeViesClient(default=VALID)
    check = services.verify_vat_number(country, raw, client=client)
    assert (check.verified, check.reason) == (False, reason)
    assert client.calls == []
    assert not VatValidation.objects.exists()


def test_the_vat_validation_country_accepts_xi_but_no_other_non_iso_code() -> None:
    row = VatValidationFactory.build(country="xi", vat_number="123456789")
    row.full_clean()
    assert row.country == "XI"
    with pytest.raises(ValidationError) as exc:
        VatValidationFactory.build(country="XJ").full_clean()
    assert "country" in exc.value.message_dict


# --- The tax context of a BT delivery


def test_a_bt_b2b_delivery_with_a_valid_xi_number_is_reverse_charged_with_n3_2(
    vendor_oss: Vendor,
) -> None:
    check = services.verify_vat_number("XI", XI_NUMBER, client=FakeViesClient(default=VALID))
    ctx = services.build_tax_context(
        vendor_oss,
        destination_country="GB",
        destination_postcode=NI_POSTCODE,
        customer_vat=check.validation,
    )
    assert check.validation is not None
    assert ctx.customer == engine.B2BValidated("XI", "123456789", check.validation.checked_at)
    d = engine.resolve_goods_tax(ctx, engine.STANDARD)
    assert (d.rate, d.reason, d.nature_code) == (Decimal("0"), "reverse_charge", "N3.2")
    assert d.intrastat_country == "XI"
    assert ctx.platform.country == "IT"


def test_a_bt_delivery_with_a_gb_only_number_is_b2c(vendor_oss: Vendor) -> None:
    check = services.verify_vat_number("GB", "GB123456789", client=FakeViesClient(default=VALID))
    assert check.validation is None
    ctx = services.build_tax_context(
        vendor_oss, destination_country="GB", destination_postcode=NI_POSTCODE
    )
    assert ctx.customer == "b2c"
    d = engine.resolve_goods_tax(ctx, engine.STANDARD)
    assert (d.rate, d.country_of_taxation) == (Decimal("20"), "XI")


def test_a_stale_xi_answer_leaves_a_consumer(vendor_oss: Vendor) -> None:
    old = VatValidationFactory.create(
        country="XI", checked_at=timezone.now() - dt.timedelta(days=30)
    )
    ctx = services.build_tax_context(
        vendor_oss, destination_country="GB", destination_postcode=NI_POSTCODE, customer_vat=old
    )
    assert ctx.customer == "b2c"
    assert engine.resolve_goods_tax(ctx, engine.STANDARD).rate == Decimal("20")
