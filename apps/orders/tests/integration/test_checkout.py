"""ORD.3 / SHP.4: `build_checkout` previews a multi-vendor cart.

Done-when: a mixed basket with two vendors, mixed VAT rates, a NeedsQuote package and an
on-request vendor gives per-vendor totals and the right blocking messages. Also: shipping VAT
follows the goods pro rata (TAX.17), the over-band policy (D-06), out of stock, missing address,
the checkout kill switch, the buyer's shipping choice, reverse charge.
"""

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from apps.cart.models import Cart
from apps.cart.tests.factories import CartFactory, CartLineFactory
from apps.catalog.models import Product
from apps.catalog.tests.factories import ProductFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog, FeatureFlag
from apps.core.tests.factories import PlatformOperatorFactory, utc
from apps.orders import checkout, engine
from apps.orders.services import place_order
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


@pytest.fixture(autouse=True)
def tax_setup() -> None:
    PlatformOperatorFactory.create(open_ended=True, valid_from=utc(2026, 1, 1), country="IT")
    for country, category, rate in (
        ("IT", tax.STANDARD, "22"),
        ("IT", tax.REDUCED, "10"),
        ("DE", tax.STANDARD, "19"),
    ):
        TaxRateFactory.create(
            country=country, category=category, rate=D(rate), valid_from=utc(2027, 1, 1)
        )


def vendor(*, shipping: bool = True, countries: tuple[str, ...] = ("IT",), **fields: Any) -> Vendor:
    """An active Italian domestic vendor with a tax profile and, by default, one IT shipping
    method: 0-5 kg 9.90, 5-10 kg 14.90 net (the default over-band policy is a quote)."""
    fields.setdefault("sells_online", True)
    found = VendorFactory.create(status=Vendor.Status.ACTIVE, country="IT", **fields)
    VendorTaxProfileFactory.create(vendor=found, valid_from=utc(2027, 1, 1))
    if shipping:
        method = ShippingMethodFactory.create(
            vendor=found,
            zone=ShippingZoneFactory.create(countries=list(countries)),
            is_default=True,
        )
        ShippingMethodTranslationFactory.create(method=method, name="Standard", status="published")
        WeightBandFactory.create(method=method, from_kg=D("0"), to_kg=D("5"), cost_net=D("9.90"))
        WeightBandFactory.create(method=method, from_kg=D("5"), to_kg=D("10"), cost_net=D("14.90"))
    return found


def add(cart: Cart, owner: Vendor, price: str, quantity: int = 1, **fields: Any) -> Product:
    values: dict[str, Any] = {
        "vendor": owner,
        "status": Product.Status.PUBLISHED,
        "price_net": D(price),
        "stock_qty": 100,
        "weight_kg": D("0.500"),
        **fields,
    }
    product = ProductFactory.create(**values)
    CartLineFactory.create(cart=cart, product=product, quantity=quantity, added_price_net=D(price))
    return product


def preview(cart: Cart, address: dict[str, Any] | None = None, **kwargs: Any) -> Any:
    return checkout.build_checkout(cart, address or ADDRESS, now=NOW, **kwargs)


def test_single_vendor_totals_and_place_vendor() -> None:
    cart = CartFactory.create()
    seller = vendor()
    add(cart, seller, "50.00", 2)  # 100.00 net, 1 kg
    result = preview(cart, email="buyer@example.com")
    assert result.placeable
    assert not result.blocks
    (package,) = result.packages
    assert package.state == engine.READY
    assert package.selected_shipping
    assert package.totals is not None
    assert (package.totals.goods_net, package.totals.shipping_net) == (D("100.00"), D("9.90"))
    # 22% of 100.00 = 22.00, shipping 9.90 -> 2.178 -> 2.18
    assert package.totals.tax_total == D("24.18")
    assert package.totals.grand_total == D("134.08")
    assert result.totals == package.totals
    (place,) = result.vendors
    assert place.shipping_net == D("9.90")
    assert place.shipping_tax is not None
    assert place.shipping_tax.rate == D("22.0000")
    assert place.shipping_choice["key"] == package.selected_shipping


def test_mixed_basket_two_vendors_mixed_vat_quote_and_on_request() -> None:
    cart = CartFactory.create()
    a = vendor()
    b = vendor()
    heavy = vendor()
    request_only = vendor(shipping=False)
    add(cart, a, "30.00")  # a: standard
    add(cart, a, "20.00", fiscal_category=Product.FiscalCategory.REDUCED)
    add(cart, b, "100.00")  # b: standard only
    add(cart, heavy, "10.00", weight_kg=D("12.000"))  # over the last band -> quote (D-06)
    add(cart, request_only, "40.00", sales_mode=Product.SalesMode.ON_REQUEST, price_net=None)
    result = preview(cart)
    by_vendor = {p.vendor_id: p for p in result.packages}
    assert [p.vendor_id for p in result.packages] == [a.pk, b.pk, heavy.pk, request_only.pk]

    a_pkg = by_vendor[a.pk]  # two rates: 30.00 at 22%, 20.00 at 10%, shipping 9.90 pro rata
    assert a_pkg.state == engine.BLOCKED  # priced, but the order cannot keep two shipping rates
    assert a_pkg.totals is not None
    assert [(p.net, p.rate) for p in a_pkg.shipping_portions] == [
        (D("5.94"), D("22.0000")),
        (D("3.96"), D("10.0000")),
    ]
    assert a_pkg.totals.goods_net == D("50.00")
    assert a_pkg.totals.tax_total == D("6.60") + D("2.00") + D("1.31") + D("0.40")
    assert [b.code for b in a_pkg.blocks] == [engine.SHIPPING_VAT_SPLIT]

    b_pkg = by_vendor[b.pk]
    assert b_pkg.state == engine.READY
    assert b_pkg.totals is not None
    assert b_pkg.totals.grand_total == D("100.00") + D("9.90") + D("24.18")

    assert by_vendor[heavy.pk].state == engine.QUOTE
    assert [x.code for x in by_vendor[heavy.pk].blocks] == [engine.NEEDS_QUOTE]
    assert by_vendor[request_only.pk].state == engine.BLOCKED
    assert [x.code for x in by_vendor[request_only.pk].blocks] == [engine.ON_REQUEST]

    assert not result.placeable
    assert result.needs_quote
    codes = {x.code for x in result.blocks}
    assert codes == {engine.SHIPPING_VAT_SPLIT, engine.NEEDS_QUOTE, engine.ON_REQUEST}
    assert result.totals == b_pkg.totals  # only the ready package is added up
    assert [p.vendor for p in result.vendors] == [b]


@pytest.mark.parametrize(
    "fields",
    [
        {"sells_online": False},  # no payout account ready (D-19, derived at read time)
        {"sales_mode_default": Vendor.SalesMode.ON_REQUEST},  # the vendor chose on request
    ],
)
def test_a_buy_line_of_a_vendor_on_request_is_an_on_request_block(fields: dict[str, Any]) -> None:
    """A `buy` product of a vendor on request is on request at checkout (VEN.6): a block, never
    a line without a price reaching the pricing (a cart from before the vendor went on request)."""
    cart = CartFactory.create()
    seller = vendor(**fields)
    add(cart, seller, "40.00")  # sales_mode buy, priced
    result = preview(cart)
    (package,) = result.packages
    assert package.state == engine.BLOCKED
    assert [x.code for x in package.blocks] == [engine.ON_REQUEST]
    assert not result.placeable


def test_shipping_vat_follows_goods_rate_of_a_reduced_package() -> None:
    cart = CartFactory.create()
    seller = vendor()
    add(cart, seller, "80.00", fiscal_category=Product.FiscalCategory.REDUCED)
    (package,) = preview(cart).packages
    assert [(p.net, p.rate, p.tax) for p in package.shipping_portions] == [
        (D("9.90"), D("10.0000"), D("0.99"))
    ]


def test_mixed_rate_shipping_vat_is_split_by_net_goods_and_the_cent_goes_on_the_largest() -> None:
    """TAX.17 / D-03 through `build_checkout`: 10.00 at 22% and 30.00 at 10% from one vendor,
    shipping 9.90. Shares are 2.475 and 7.425: 2.48 rounds half up, so the largest base (the 10%
    goods) takes 7.42 and the portions still add up to 9.90."""
    cart = CartFactory.create()
    seller = vendor()
    add(cart, seller, "10.00")
    add(cart, seller, "30.00", fiscal_category=Product.FiscalCategory.REDUCED)
    (package,) = preview(cart).packages
    assert [(p.net, p.rate, p.tax) for p in package.shipping_portions] == [
        (D("2.48"), D("22.0000"), D("0.55")),
        (D("7.42"), D("10.0000"), D("0.74")),
    ]
    assert sum((p.net for p in package.shipping_portions), start=D("0")) == D("9.90")
    assert package.totals is not None
    assert package.totals.tax_total == D("2.20") + D("3.00") + D("1.29")


def test_weight_above_the_last_band_needs_a_quote_not_the_last_band_price() -> None:
    cart = CartFactory.create()
    seller = vendor()
    add(cart, seller, "10.00", weight_kg=D("10.500"))
    result = preview(cart)
    (package,) = result.packages
    assert package.state == engine.QUOTE
    assert package.totals is None
    assert result.needs_quote
    assert not result.placeable
    assert result.vendors == []


def test_out_of_stock_and_not_enough_stock() -> None:
    cart = CartFactory.create()
    seller = vendor()
    gone = add(cart, seller, "10.00", stock_qty=0, stock_policy=Product.StockPolicy.TRACK)
    few = add(cart, seller, "10.00", 5, stock_qty=3, stock_policy=Product.StockPolicy.TRACK)
    result = preview(cart)
    blocks = {b.product_id: b for b in result.blocks}
    assert blocks[gone.pk].code == engine.OUT_OF_STOCK
    assert blocks[few.pk].message == "Only 3 available."
    assert not result.placeable


def test_missing_address_fields_block_without_a_crash() -> None:
    cart = CartFactory.create()
    add(cart, vendor(), "10.00")
    result = preview(cart, {"line1": "1 Via Test", "country": "IT"})
    (block,) = [b for b in result.blocks if b.code == engine.ADDRESS_MISSING]
    assert block.fields == ("city", "postcode")
    assert not result.placeable


def test_invalid_country_is_an_address_block() -> None:
    cart = CartFactory.create()
    add(cart, vendor(), "10.00")
    result = preview(cart, {**ADDRESS, "country": "XK"})
    assert [b.fields for b in result.blocks if b.code == engine.ADDRESS_MISSING] == [("country",)]


def test_empty_cart() -> None:
    result = preview(CartFactory.create())
    assert [b.code for b in result.blocks] == [engine.EMPTY_CART]
    assert not result.placeable


def test_checkout_kill_switch_blocks_everything() -> None:
    cart = CartFactory.create()
    add(cart, vendor(), "10.00")
    FeatureFlag.objects.filter(name="checkout").update(state=FeatureFlag.State.OFF)
    result = preview(cart)
    assert engine.CHECKOUT_OFF in {b.code for b in result.blocks}
    assert not result.placeable


def test_country_without_a_shipping_zone_needs_a_quote_or_blocks() -> None:
    cart = CartFactory.create()
    add(cart, vendor(), "10.00")
    us = {**ADDRESS, "country": "US", "postcode": "10001"}
    assert [b.code for b in preview(cart, us).blocks] == [engine.NEEDS_QUOTE]
    off = preview(cart, us, quotes_enabled=False)
    assert [b.code for b in off.blocks] == [engine.SHIPPING_BLOCKED]
    assert not off.placeable


def test_buyer_choice_of_shipping_rate_and_an_unknown_key() -> None:
    cart = CartFactory.create()
    seller = vendor()
    fast = ShippingMethodFactory.create(
        vendor=seller, zone=seller.shipping_methods.get().zone, express=True
    )
    ShippingMethodTranslationFactory.create(method=fast, name="Express", status="published")
    WeightBandFactory.create(method=fast, from_kg=D("0"), to_kg=D("5"), cost_net=D("19.90"))
    add(cart, seller, "50.00")
    default = preview(cart).packages[0]
    assert default.selected_shipping != "express"
    chosen = preview(cart, shipping_choices={seller.pk: "express"}).packages[0]
    assert chosen.selected_shipping == "express"
    assert chosen.totals is not None
    assert chosen.totals.shipping_net == D("19.90")
    bad = preview(cart, shipping_choices={seller.pk: "nope"})
    assert [b.code for b in bad.blocks] == [engine.SHIPPING_CHOICE_INVALID]
    assert not bad.placeable


def test_a_vendor_without_a_tax_profile_blocks_only_its_package() -> None:
    cart = CartFactory.create()
    good = vendor()
    broken = VendorFactory.create(status=Vendor.Status.ACTIVE, sells_online=True, country="IT")
    add(cart, good, "10.00")
    add(cart, broken, "10.00")
    result = preview(cart)
    states = {p.vendor_id: p.state for p in result.packages}
    assert states == {good.pk: engine.READY, broken.pk: engine.BLOCKED}
    assert [b.code for b in result.blocks] == [engine.TAX_BLOCKED]


def test_validated_business_in_another_member_state_is_reverse_charge() -> None:
    cart = CartFactory.create()
    seller = vendor(countries=("IT", "DE"))
    add(cart, seller, "100.00")
    vat = VatValidationFactory.create(country="DE", result=tax.VAT_VALID, checked_at=NOW)
    de = {**ADDRESS, "country": "DE", "postcode": "10115"}
    result = preview(cart, de, customer_vat=vat)
    (package,) = result.packages
    assert package.state == engine.READY
    assert package.totals is not None
    assert package.totals.tax_total == D("0.00")
    assert [p.rate for p in package.shipping_portions] == [D("0")]
    assert result.customer_tax_status == "b2b"
    assert result.customer_vat_number == vat.vat_number
    assert result.placeable
    consumer = preview(cart, de)  # no VAT number: destination (German) VAT, consumer
    assert consumer.customer_tax_status == "b2c"
    assert consumer.packages[0].totals is not None
    assert consumer.packages[0].totals.tax_total > 0


def test_a_ready_preview_places_an_order_with_the_previewed_totals() -> None:
    cart = CartFactory.create()
    seller = vendor()
    add(cart, seller, "50.00", 2)
    commission = {
        "rule_id": "default",
        "version": 1,
        "percent": "10.00",
        "base": "goods",
        "fee_percent": "1.40",
        "fee_fixed": "0.25",
        "fee_base": "gross",
    }
    result = preview(cart, email="buyer@example.com", commission_snapshots={seller.pk: commission})
    assert result.placeable
    order, _ = place_order(result, ctx=CTX, now=NOW)
    assert order is not None
    assert order.goods_net == result.totals.goods_net
    assert order.tax_total == result.totals.tax_total
    assert order.grand_total == result.totals.grand_total
