"""TAX.3: the tax context is built from the rate table, the territories, the vendor's profile at
the date, the platform operator and the VIES answer; then `resolve_goods_tax` decides."""

import datetime as dt
from decimal import Decimal

import pytest

from apps.core.tests.factories import PlatformOperatorFactory, utc
from apps.tax import engine, services
from apps.tax.tests.factories import (
    TaxRateFactory,
    TerritoryFactory,
    VatValidationFactory,
    VendorTaxProfileFactory,
)
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

AT = utc(2027, 6, 1)


def rate(country: str, category: str, value: str, *, valid_to: dt.datetime | None = None) -> None:
    TaxRateFactory.create(
        country=country,
        category=category,
        rate=Decimal(value),
        valid_from=utc(2027, 1, 1),
        valid_to=valid_to,
    )


@pytest.fixture
def vendor_oss() -> Vendor:
    PlatformOperatorFactory.create(open_ended=True, valid_from=utc(2026, 1, 1), country="IT")
    vendor = VendorFactory.create(country="IT")
    VendorTaxProfileFactory.create(vendor=vendor, oss=True, valid_from=utc(2027, 1, 1))
    rate("IT", "standard", "22")
    rate("DE", "standard", "19", valid_to=utc(2028, 1, 1))
    return vendor


def test_a_b2c_sale_is_taxed_at_the_delivery_country_rate(vendor_oss: Vendor) -> None:
    ctx = services.build_tax_context(vendor_oss, destination_country="de", at=AT)
    d = engine.resolve_goods_tax(ctx, engine.STANDARD)
    assert (d.rate, d.reason) == (Decimal("19"), "standard_destination")
    assert ctx.platform.country == "IT"
    assert ctx.on == AT.date()


def test_a_valid_vies_answer_makes_the_customer_a_validated_business(vendor_oss: Vendor) -> None:
    vat = VatValidationFactory.create(country="DE", checked_at=AT - dt.timedelta(hours=1))
    ctx = services.build_tax_context(vendor_oss, destination_country="DE", customer_vat=vat, at=AT)
    assert ctx.customer == engine.B2BValidated("DE", vat.vat_number, vat.checked_at)
    assert engine.resolve_goods_tax(ctx, engine.STANDARD).reason == "reverse_charge"


@pytest.mark.parametrize("result", [engine.VAT_INVALID, engine.VAT_UNAVAILABLE])
def test_t2_an_unverified_answer_leaves_the_customer_a_consumer(
    vendor_oss: Vendor, result: str
) -> None:
    vat = VatValidationFactory.create(country="DE", result=result)
    ctx = services.build_tax_context(vendor_oss, destination_country="DE", customer_vat=vat, at=AT)
    assert ctx.customer == "b2c"
    assert engine.resolve_goods_tax(ctx, engine.STANDARD).rate == Decimal("19")


def test_the_rates_valid_at_the_date_are_used(vendor_oss: Vendor) -> None:
    TaxRateFactory.create(
        country="DE",
        category="standard",
        rate=Decimal("21"),
        valid_from=utc(2028, 1, 1),
        valid_to=None,
    )
    old = services.build_tax_context(vendor_oss, destination_country="DE", at=AT)
    assert engine.resolve_goods_tax(old, engine.STANDARD).rate == Decimal("19")


def test_t6_a_missing_rate_is_a_blocking_error(vendor_oss: Vendor) -> None:
    ctx = services.build_tax_context(vendor_oss, destination_country="DE", at=AT)
    with pytest.raises(engine.MissingTaxRate):
        engine.resolve_goods_tax(ctx, engine.REDUCED)


def test_t15_stored_territories_are_read(vendor_oss: Vendor) -> None:
    TerritoryFactory.create(
        code="ES-CN", country="ES", vat_status=engine.OUTSIDE_VAT_AREA, postcode_patterns=["35*"]
    )
    ctx = services.build_tax_context(
        vendor_oss, destination_country="ES", destination_postcode="35001", at=AT
    )
    assert engine.resolve_goods_tax(ctx, engine.STANDARD).reason == "outside_vat_territory"


def test_an_own_rates_territory_rate_is_keyed_by_its_code(vendor_oss: Vendor) -> None:
    territory = TerritoryFactory.create(
        code="PT-20", country="PT", vat_status=engine.OWN_RATES, postcode_patterns=["9000..9499"]
    )
    rate("PT", "standard", "23")
    TaxRateFactory.create(
        country="PT",
        territory=territory,
        category="standard",
        rate=Decimal("22"),
        valid_from=utc(2027, 1, 1),
        valid_to=None,
    )
    ctx = services.build_tax_context(
        vendor_oss, destination_country="PT", destination_postcode="9050", at=AT
    )
    assert engine.resolve_goods_tax(ctx, engine.STANDARD).rate == Decimal("22")


def test_an_exempt_vendor_resolves_to_its_exemption() -> None:
    PlatformOperatorFactory.create(open_ended=True, valid_from=utc(2026, 1, 1))
    profile = VendorTaxProfileFactory.create(art74=True, valid_from=utc(2027, 1, 1))
    ctx = services.build_tax_context(profile.vendor, destination_country="DE", at=AT)
    assert engine.resolve_goods_tax(ctx, engine.STANDARD).reason == "art74_publishing"


def test_the_platform_country_comes_from_the_operator_not_a_literal() -> None:
    PlatformOperatorFactory.create(open_ended=True, valid_from=utc(2026, 1, 1), country="DE")
    profile = VendorTaxProfileFactory.create(valid_from=utc(2027, 1, 1))
    ctx = services.build_tax_context(profile.vendor, destination_country="FR", at=AT)
    assert ctx.platform.country == "DE"


def test_a_vendor_without_a_profile_is_an_error() -> None:
    PlatformOperatorFactory.create(open_ended=True, valid_from=utc(2026, 1, 1))
    with pytest.raises(services.NoVendorTaxProfile):
        services.build_tax_context(VendorFactory.create(), destination_country="DE", at=AT)
