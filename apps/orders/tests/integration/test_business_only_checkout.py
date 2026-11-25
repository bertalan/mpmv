"""VEN.41: a business-only seller refuses a consumer order at checkout.

Done-when (slice 3): with the flag on, a consumer checkout and a business checkout with an
invalid, unavailable (VIES down), stale or missing VAT number get `Blocked` for that vendor's
package, before payment; a verified business checkout places the order; in a mixed cart only the
flagged vendor's package is blocked and the other vendor's totals do not change; an allowed order
keeps its prices and tax.
"""

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest
from django.utils import translation

from apps.cart.models import Cart
from apps.cart.tests.factories import CartFactory, CartLineFactory
from apps.catalog.models import Product
from apps.catalog.tests.factories import ProductFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.core.tests.factories import PlatformOperatorFactory, utc
from apps.orders import checkout, engine
from apps.orders.services import place_order
from apps.orders.ui.present import block_message
from apps.shipping.tests.factories import (
    ShippingMethodFactory,
    ShippingMethodTranslationFactory,
    ShippingZoneFactory,
    WeightBandFactory,
)
from apps.tax import engine as tax
from apps.tax.tests.factories import TaxRateFactory, VatValidationFactory, VendorTaxProfileFactory
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

D = Decimal
NOW = dt.datetime(2027, 3, 1, 10, 0, tzinfo=dt.UTC)
CTX = AuditContext(actor_label="system:test", source=AuditLog.Source.SYSTEM)
ADDRESS = {"line1": "1 Via Test", "city": "Roma", "postcode": "00100", "country": "IT"}
COMMISSION = {
    "rule_id": "default",
    "version": 1,
    "percent": "10.00",
    "base": "goods",
    "fee_percent": "1.40",
    "fee_fixed": "0.25",
    "fee_base": "gross",
}


@pytest.fixture(autouse=True)
def tax_setup() -> None:
    PlatformOperatorFactory.create(open_ended=True, valid_from=utc(2026, 1, 1), country="IT")
    TaxRateFactory.create(
        country="IT", category=tax.STANDARD, rate=D("22"), valid_from=utc(2027, 1, 1)
    )


def seller(**fields: Any) -> Vendor:
    """An active Italian vendor with a tax profile and one IT shipping method (9.90 net)."""
    found = VendorFactory.create(
        status=Vendor.Status.ACTIVE, country="IT", sells_online=True, **fields
    )
    VendorTaxProfileFactory.create(vendor=found, valid_from=utc(2027, 1, 1))
    method = ShippingMethodFactory.create(
        vendor=found, zone=ShippingZoneFactory.create(countries=["IT"]), is_default=True
    )
    ShippingMethodTranslationFactory.create(method=method, name="Standard", status="published")
    WeightBandFactory.create(method=method, from_kg=D("0"), to_kg=D("5"), cost_net=D("9.90"))
    return found


def add(cart: Cart, owner: Vendor, price: str) -> Product:
    product = ProductFactory.create(
        vendor=owner,
        status=Product.Status.PUBLISHED,
        price_net=D(price),
        stock_qty=100,
        weight_kg=D("0.500"),
    )
    CartLineFactory.create(cart=cart, product=product, quantity=1, added_price_net=D(price))
    return product


def preview(cart: Cart, **kwargs: Any) -> Any:
    return checkout.build_checkout(cart, ADDRESS, now=NOW, email="buyer@example.com", **kwargs)


def italian_vat(**fields: Any) -> Any:
    values: dict[str, Any] = {"country": "IT", "result": tax.VAT_VALID, "checked_at": NOW}
    return VatValidationFactory.create(**{**values, **fields})


def test_a_consumer_checkout_with_a_business_only_seller_is_blocked() -> None:
    cart = CartFactory.create()
    flagged = seller(business_only=True)
    add(cart, flagged, "50.00")
    result = preview(cart)
    (package,) = result.packages
    assert package.state == engine.BLOCKED
    assert [b.code for b in package.blocks] == [engine.BUSINESS_ONLY]
    assert [b.code for b in result.blocks] == [engine.BUSINESS_ONLY]
    assert not result.vendors
    assert not result.placeable
    assert result.totals is None  # refused before payment: nothing to pay or place


@pytest.mark.parametrize(
    "fields",
    [
        {"result": tax.VAT_INVALID},
        {"result": tax.VAT_UNAVAILABLE},  # VIES down: fail closed
        {"checked_at": NOW - dt.timedelta(days=30)},  # a stale valid answer
    ],
    ids=["invalid", "vies-down", "stale"],
)
def test_a_business_without_a_verified_vat_number_is_blocked(fields: dict[str, Any]) -> None:
    cart = CartFactory.create()
    add(cart, seller(business_only=True), "50.00")
    result = preview(cart, customer_vat=italian_vat(**fields))
    assert [p.state for p in result.packages] == [engine.BLOCKED]
    assert [b.code for b in result.blocks] == [engine.BUSINESS_ONLY]
    assert not result.placeable


def test_a_verified_business_buys_from_a_business_only_seller_at_unchanged_prices() -> None:
    cart = CartFactory.create()
    flagged = seller(business_only=True)
    add(cart, flagged, "50.00")
    vat = italian_vat()
    result = preview(cart, customer_vat=vat, commission_snapshots={flagged.pk: COMMISSION})
    (package,) = result.packages
    assert package.state == engine.READY
    assert result.placeable
    assert result.customer_tax_status == "b2b"
    # The flag changes no price or tax: domestic B2B at 22%, as for an unflagged seller.
    plain_cart = CartFactory.create()
    plain = seller()
    add(plain_cart, plain, "50.00")
    plain_result = preview(plain_cart, customer_vat=vat)
    assert package.totals == plain_result.packages[0].totals
    order, _ = place_order(result, ctx=CTX, now=NOW)
    assert order is not None


def test_a_mixed_cart_blocks_only_the_business_only_package() -> None:
    cart = CartFactory.create()
    flagged = seller(business_only=True)
    plain = seller()
    add(cart, flagged, "50.00")
    add(cart, plain, "20.00")
    result = preview(cart)
    states = {p.vendor_id: p.state for p in result.packages}
    assert states == {flagged.pk: engine.BLOCKED, plain.pk: engine.READY}
    assert [(b.code, b.vendor_id) for b in result.blocks] == [(engine.BUSINESS_ONLY, flagged.pk)]
    assert [v.vendor.pk for v in result.vendors] == [plain.pk]
    assert not result.placeable  # the buyer removes the held items or gives a verified number
    # The other vendor's package is priced as it would be alone.
    alone = CartFactory.create()
    add(alone, plain, "20.00")
    assert result.packages[1].totals == preview(alone).packages[0].totals


def test_an_unflagged_seller_still_sells_to_consumers() -> None:
    cart = CartFactory.create()
    add(cart, seller(), "50.00")
    result = preview(cart)
    assert result.placeable
    assert not result.blocks


def test_the_refusal_is_shown_before_the_address() -> None:
    cart = CartFactory.create()
    add(cart, seller(business_only=True), "50.00")
    result = checkout.build_checkout(cart, {}, now=NOW)
    assert engine.BUSINESS_ONLY in [b.code for b in result.packages[0].blocks]


def test_the_refusal_message_is_translatable() -> None:
    cart = CartFactory.create()
    add(cart, seller(business_only=True), "50.00")
    (block,) = preview(cart).blocks
    with translation.override("en"):
        english = block_message(block)
    assert "businesses only" in english
    with translation.override("it"):
        assert block_message(block) != english
