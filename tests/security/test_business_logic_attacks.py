"""SEC.5 attack scenarios, category "Business logic" (owner security wave, item 10, part 3a).

Each test plays the attacker against our own code (cart, catalogue API, shipping import) and
asserts that the attack fails and leaves nothing behind. Cases an existing test already covers
are not repeated: tests/security/README.md names the covering test.

Left out on purpose, with the test that already proves it:

- a forged or stale price at checkout is refused and never repriced:
  `apps/orders/tests/integration/test_place_order.py::test_a_changed_price_refuses_the_order_and_never_reprices`;
- two buyers racing for the last unit give one order, and a cart hold races an order:
  `apps/orders/tests/integration/test_place_order.py` (the two concurrency tests);
- more units than the stock, in one add or across adds:
  `apps/cart/tests/integration/test_services.py::test_adding_more_than_the_stock_is_refused`;
- a double-submitted sign-in merging the guest cart twice:
  `apps/cart/tests/integration/test_services.py::test_merging_a_guest_cart_that_was_already_adopted_is_a_no_op`;
- hostile text in shipping files (formulas, markup, control characters):
  `tests/integration/test_hostile_input_outputs.py`.

Coupons, refunds, payouts and payments are lane A's (orders, payments, ledger); their attack
scenarios are listed on the PR as 3a-2 and are not written here.
"""

import threading
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import Client
from django.urls import reverse

from apps.api.tests.factories import ApiClientFactory
from apps.cart import engine as cart_engine
from apps.cart import services as cart
from apps.cart.models import Cart, CartLine
from apps.cart.tests.factories import CartFactory
from apps.catalog.models import Product, Variant
from apps.catalog.tests.factories import ProductFactory, VariantFactory, clothing_attributes
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.shipping import config_format as fmt
from apps.shipping.config_io import export_config, import_config
from apps.shipping.models import ShippingMethod, WeightBand
from apps.shipping.tests.factories import (
    CarrierFactory,
    ShippingMethodFactory,
    ShippingZoneFactory,
)
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

PRODUCTS_API = "/api/v1/catalog/products"
CTX = AuditContext(actor_id=1, actor_label="staff:attack", source="admin", ip="127.0.0.1")


def sellable(**fields: Any) -> Product:
    """A published `buy` product of an active, online vendor, 10 in stock, 10.00 net."""
    values: dict[str, Any] = {
        "vendor": VendorFactory.create(status=Vendor.Status.ACTIVE, sells_online=True),
        "status": Product.Status.PUBLISHED,
        "stock_qty": 10,
        "price_net": Decimal("10.00"),
        **fields,
    }
    return ProductFactory.create(**values)


def guest_cart(key: str = "attacker-session") -> Cart:
    return CartFactory.create(session_key=key)


def add_post(client: Client, **data: object) -> Any:
    return client.post(reverse("orders:cart_add"), data)


# --- A price or VAT sent by the client --------------------------------------------------------


@pytest.mark.parametrize("smuggled", ["price", "price_net", "unit_price", "vat_rate", "tax"])
@pytest.mark.django_db
def test_the_cart_service_takes_no_price_or_vat_from_the_caller(smuggled: str) -> None:
    product = sellable()
    add: Callable[..., Any] = cart.add_to_cart
    with pytest.raises(TypeError):
        add(guest_cart(), product, quantity=1, **{smuggled: Decimal("0.01")})
    assert not CartLine.objects.exists()


@pytest.mark.parametrize("smuggled", ["price", "price_net", "unit_price", "vat_rate", "total"])
@pytest.mark.django_db
def test_the_add_to_cart_form_ignores_a_posted_price(client: Client, smuggled: str) -> None:
    product = sellable(price_net=Decimal("80.00"))
    response = add_post(client, product=str(product.public_id), quantity="1", **{smuggled: "0.01"})
    assert response.status_code == 302
    line = CartLine.objects.get()
    assert line.added_price_net == Decimal("80.00")


@pytest.mark.django_db
def test_a_tampered_display_price_in_the_cart_is_never_the_price() -> None:
    """A line whose stored display price was rewritten is priced from the catalogue, and the
    difference is flagged to the customer."""
    product = sellable(price_net=Decimal("80.00"))
    basket = guest_cart()
    cart.add_to_cart(basket, product, quantity=2)
    CartLine.objects.filter(cart=basket).update(added_price_net=Decimal("0.01"))

    [package] = cart.vendor_packages(basket)
    [line] = package.lines
    assert line.unit_price_net == Decimal("80.00")
    assert line.line_net == Decimal("160.00")
    assert line.price_changed


@pytest.mark.django_db
def test_a_variant_price_comes_from_the_variant_not_from_the_caller() -> None:
    product = sellable(kind=Product.Kind.VARIABLE, price_net=Decimal("30.00"))
    variant = VariantFactory.create(
        product=product, price_net_override=Decimal("45.00"), stock_qty=5
    )
    basket = guest_cart()
    cart.add_to_cart(basket, product, variant=variant)
    assert CartLine.objects.get(cart=basket).added_price_net == Decimal("45.00")


def _post_product(client: Client, payload: dict[str, Any]) -> Any:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    _, key = ApiClientFactory.create_with_key(
        vendor=vendor, scopes=["catalog:read", "catalog:write"]
    )
    return client.post(
        PRODUCTS_API,
        payload,
        content_type="application/json",
        headers={"Authorization": f"Bearer {key}"},
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("price_net", "-10.00"),
        ("price_net", "0.001"),
        ("price_net", "NaN"),
        ("price_net", "Infinity"),
        ("price_net", "1e30"),
        ("price_net", "10000000000.00"),
        ("fiscal_category", "zero_rated"),
    ],
)
@pytest.mark.django_db
def test_the_api_refuses_absurd_prices_and_unknown_tax_categories(
    client: Client, field: str, value: str
) -> None:
    """A vendor key cannot create a product at a negative, non-finite or overflowing price,
    nor pick a tax treatment outside the fiscal categories: a 4xx problem, never a 500 and
    never a row."""
    payload = {
        "sku": "ATK-1",
        "price_net": "19.90",
        "translations": [{"language": "en", "title": "Probe"}],
        field: value,
    }
    response = _post_product(client, payload)
    assert 400 <= response.status_code < 500, response.content
    assert not Product.objects.filter(sku="ATK-1").exists()


@pytest.mark.parametrize(
    ("variant_field", "value"),
    [("price_net_override", "-5.00"), ("stock_qty", 10**12), ("stock_qty", -1)],
)
@pytest.mark.django_db
def test_the_api_refuses_absurd_variant_prices_and_stock(
    client: Client, variant_field: str, value: object
) -> None:
    clothing_attributes()
    variant: dict[str, Any] = {"sku": "ATK-2-S", "options": {"size": "s"}, variant_field: value}
    payload = {
        "sku": "ATK-2",
        "kind": "variable",
        "price_net": "19.90",
        "translations": [{"language": "en", "title": "Probe"}],
        "attributes": [{"attribute": "size", "values": ["s"], "variant_axis": True}],
        "variants": [variant],
    }
    response = _post_product(client, payload)
    assert 400 <= response.status_code < 500, response.content
    assert not Product.objects.filter(sku="ATK-2").exists()
    assert not Variant.objects.filter(sku="ATK-2-S").exists()


# --- Negative, zero, fractional or huge quantities --------------------------------------------


@pytest.mark.parametrize("quantity", [0, -1, -(2**31), -(10**30)])
@pytest.mark.django_db
def test_adding_zero_or_negative_units_is_refused(quantity: int) -> None:
    with pytest.raises(ValidationError):
        cart.add_to_cart(guest_cart(), sellable(), quantity=quantity)
    assert not CartLine.objects.exists()


@pytest.mark.parametrize("quantity", [cart_engine.MAX_LINE_QUANTITY + 1, 2**31, 2**63, 10**30])
@pytest.mark.django_db
def test_huge_quantities_are_refused_before_the_database(quantity: int) -> None:
    """Above the per-line ceiling the service refuses with a message, even with unlimited
    backorders: no integer overflow, no 500."""
    product = sellable(
        stock_policy=Product.StockPolicy.ALWAYS_OPEN, stock_status=Product.StockStatus.ON_BACKORDER
    )
    basket = guest_cart()
    with pytest.raises(ValidationError, match="available"):
        cart.add_to_cart(basket, product, quantity=quantity)
    cart.add_to_cart(basket, product, quantity=1)
    line = CartLine.objects.get(cart=basket)
    with pytest.raises(ValidationError, match="available"):
        cart.set_line_quantity(line, quantity)
    line.refresh_from_db()
    assert line.quantity == 1


@pytest.mark.django_db
def test_repeated_adds_cannot_creep_past_the_ceiling() -> None:
    product = sellable(
        stock_policy=Product.StockPolicy.ALWAYS_OPEN, stock_status=Product.StockStatus.ON_BACKORDER
    )
    basket = guest_cart()
    cart.add_to_cart(basket, product, quantity=cart_engine.MAX_LINE_QUANTITY)
    with pytest.raises(ValidationError):
        cart.add_to_cart(basket, product, quantity=1)
    assert CartLine.objects.get(cart=basket).quantity == cart_engine.MAX_LINE_QUANTITY


@pytest.mark.parametrize("quantity", [-1, -(10**30)])
@pytest.mark.django_db
def test_a_negative_line_quantity_removes_the_line_and_never_stores_a_credit(
    quantity: int,
) -> None:
    basket = guest_cart()
    cart.add_to_cart(basket, sellable(), quantity=2)
    cart.set_line_quantity(CartLine.objects.get(cart=basket), quantity)
    assert not CartLine.objects.filter(cart=basket).exists()


@pytest.mark.parametrize("quantity", [1.5, Decimal("1.5"), Decimal("2"), 2.0, True])
@pytest.mark.django_db
def test_fractional_and_non_integer_quantities_are_refused(quantity: Any) -> None:
    """1.5 must not be truncated to 1 by the database driver, and a boolean is not a count."""
    basket = guest_cart()
    product = sellable()
    with pytest.raises(ValidationError, match="whole number"):
        cart.add_to_cart(basket, product, quantity=quantity)
    cart.add_to_cart(basket, product, quantity=1)
    line = CartLine.objects.get(cart=basket)
    with pytest.raises(ValidationError, match="whole number"):
        cart.set_line_quantity(line, quantity)
    line.refresh_from_db()
    assert line.quantity == 1


@pytest.mark.parametrize(
    "quantity", ["0", "-3", "1.5", "1e3", "NaN", "", " ", "9" * 5000, "2; DROP TABLE", "\x00"]
)
@pytest.mark.django_db
def test_the_add_to_cart_form_refuses_hostile_quantities(client: Client, quantity: str) -> None:
    """Whatever the quantity field holds, the answer is a page (never a 500) and no line holds
    more than one unit or a non-positive count. An empty field means 1, by design."""
    product = sellable()
    response = add_post(client, product=str(product.public_id), quantity=quantity)
    assert response.status_code < 500
    assert all(1 <= line.quantity <= 1 for line in CartLine.objects.all())


# --- Cross-vendor and out-of-scope cart manipulation ------------------------------------------


@pytest.mark.parametrize(
    "fields",
    [
        {"status": Product.Status.DRAFT},
        {"status": Product.Status.RETIRED},
        {"vendor": None},
    ],
    ids=["draft", "retired", "vendor-on-request"],
)
@pytest.mark.django_db
def test_a_product_that_is_not_for_sale_cannot_be_put_in_a_cart_by_its_id(
    client: Client, fields: dict[str, Any]
) -> None:
    """The product page hides the button, but the POST takes any `public_id`: the service
    re-checks that the product is published, `buy` and of an active vendor."""
    if fields.get("vendor", 1) is None:
        fields = {"vendor": VendorFactory.create(status=Vendor.Status.ACTIVE, sells_online=False)}
    product = sellable(**fields)
    add_post(client, product=str(product.public_id), quantity="1")
    assert not CartLine.objects.exists()


@pytest.mark.django_db
def test_a_suspended_vendors_product_cannot_be_put_in_a_cart(client: Client) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.SUSPENDED, sells_online=True)
    product = sellable(vendor=vendor)
    add_post(client, product=str(product.public_id), quantity="1")
    assert not CartLine.objects.exists()


@pytest.mark.django_db
def test_another_products_variant_cannot_be_priced_into_this_products_line(
    client: Client,
) -> None:
    """Posting product A with the SKU of product B's cheap variant must not borrow B's price
    or stock: the variant is looked up among A's own variants only."""
    expensive = sellable(kind=Product.Kind.VARIABLE, price_net=Decimal("500.00"))
    VariantFactory.create(product=expensive, stock_qty=5)
    cheap = sellable(kind=Product.Kind.VARIABLE, price_net=Decimal("1.00"))
    cheap_variant = VariantFactory.create(product=cheap, price_net_override=Decimal("1.00"))
    add_post(client, product=str(expensive.public_id), variant=cheap_variant.sku, quantity="1")
    assert not CartLine.objects.exists()


@pytest.mark.django_db
def test_a_variant_posted_for_a_simple_product_is_refused() -> None:
    simple = sellable()
    foreign = VariantFactory.create(
        product=sellable(kind=Product.Kind.VARIABLE), price_net_override=Decimal("0.50")
    )
    with pytest.raises(ValidationError):
        cart.add_to_cart(guest_cart(), simple, variant=foreign, quantity=1)
    assert not CartLine.objects.exists()


@pytest.mark.django_db
def test_a_cart_with_lines_of_two_vendors_is_split_per_vendor_and_priced_per_line() -> None:
    """One cart may hold two vendors' goods; each package carries only its own vendor's lines
    at that vendor's catalogue prices (a cheap line cannot lower the other vendor's price)."""
    cheap, dear = sellable(price_net=Decimal("1.00")), sellable(price_net=Decimal("90.00"))
    basket = guest_cart()
    cart.add_to_cart(basket, cheap, quantity=3)
    cart.add_to_cart(basket, dear, quantity=1)
    packages = cart.vendor_packages(basket)
    totals = {p.vendor_id: p.goods_net for p in packages}
    assert len(packages) == 2
    assert totals == {cheap.vendor_id: Decimal("3.00"), dear.vendor_id: Decimal("90.00")}
    assert all(len(p.lines) == 1 for p in packages)


@pytest.mark.django_db
def test_another_visitors_cart_is_never_adopted_through_the_session(client: Client) -> None:
    """A guest who adds to the cart gets a cart bound to their own session; the cart of
    another session stays untouched."""
    victim = guest_cart("victim-session")
    product = sellable()
    add_post(client, product=str(product.public_id), quantity="1")
    assert not CartLine.objects.filter(cart=victim).exists()
    assert CartLine.objects.count() == 1


# --- Double submit and stock races ------------------------------------------------------------


def _in_thread(work: Callable[[], object]) -> tuple[threading.Thread, list[object]]:
    """Run `work` on its own database connection; the list gets its result or its error."""
    outcome: list[object] = []

    def run() -> None:
        try:
            outcome.append(work())
        except Exception as exc:
            outcome.append(exc)
        finally:
            connection.close()

    thread = threading.Thread(target=run)
    thread.start()
    return thread, outcome


def _race(*works: Callable[[], object]) -> list[object]:
    """Start every `work` at the same moment on its own connection; their outcomes."""
    barrier = threading.Barrier(len(works))

    def synced(work: Callable[[], object]) -> Callable[[], object]:
        def run() -> object:
            barrier.wait(timeout=10)
            return work()

        return run

    started = [_in_thread(synced(work)) for work in works]
    outcomes: list[object] = []
    for thread, outcome in started:
        thread.join(timeout=20)
        assert not thread.is_alive(), "a racing transaction never finished"
        outcomes.extend(outcome)
    return outcomes


@pytest.mark.django_db(transaction=True)
def test_a_double_submitted_add_cannot_exceed_the_stock() -> None:
    """Two "add 2" for the same cart at once, with 3 in stock: the cart lock serialises them,
    so one is refused and the line holds 2, never 4."""
    product = sellable(stock_qty=3)
    basket = guest_cart()
    outcomes = _race(
        lambda: cart.add_to_cart(basket, product, quantity=2),
        lambda: cart.add_to_cart(basket, product, quantity=2),
    )
    assert sum(isinstance(outcome, ValidationError) for outcome in outcomes) == 1, outcomes
    assert CartLine.objects.get(cart=basket).quantity == 2


@pytest.mark.django_db(transaction=True)
def test_a_double_submitted_first_visit_creates_one_cart() -> None:
    outcomes = _race(
        lambda: cart.get_or_create_cart(session_key="double-submit"),
        lambda: cart.get_or_create_cart(session_key="double-submit"),
    )
    assert all(isinstance(outcome, Cart) for outcome in outcomes), outcomes
    assert Cart.objects.filter(session_key="double-submit").count() == 1


@pytest.mark.django_db(transaction=True)
def test_two_carts_may_both_hold_the_last_unit_and_the_stock_is_untouched() -> None:
    """By design the cart does not reserve stock (the order's StockReservation does, proven by
    `apps/orders/tests/integration/test_place_order.py`): both carts take the last unit and the
    stock itself is never decremented by a cart."""
    product = sellable(stock_qty=1)
    outcomes = _race(
        lambda: cart.add_to_cart(guest_cart("buyer-1"), product, quantity=1),
        lambda: cart.add_to_cart(guest_cart("buyer-2"), product, quantity=1),
    )
    assert not any(isinstance(outcome, Exception) for outcome in outcomes), outcomes
    product.refresh_from_db()
    assert product.stock_qty == 1


# --- Shipping configuration edge cases --------------------------------------------------------


@pytest.fixture
def shop() -> Vendor:
    CarrierFactory.create(code="main")
    vendor = VendorFactory.create()
    zone = ShippingZoneFactory.create(vendor=vendor, code="home", countries=["FR"])
    ShippingMethodFactory.create(vendor=vendor, zone=zone)
    return vendor


def _method(document: dict[str, Any]) -> dict[str, Any]:
    method: dict[str, Any] = document["methods"][0]
    return method


def _band(**values: object) -> dict[str, Any]:
    return {"from_kg": "0", "to_kg": "5", "cost_net": "4.00", **values}


@pytest.mark.parametrize(
    "bands",
    [
        [_band(cost_net="-4.00")],
        [_band(surcharge_net="-1.00")],
        [_band(from_kg="5", to_kg="1")],
        [_band(from_kg="-1")],
        [_band(), _band(from_kg="3", to_kg="9")],
        [_band(cost_net="NaN")],
        [_band(cost_net="1e30")],
    ],
    ids=[
        "negative-cost",
        "negative-surcharge",
        "inverted-range",
        "negative-weight",
        "overlap",
        "nan-cost",
        "overflow-cost",
    ],
)
@pytest.mark.django_db
def test_an_import_with_absurd_bands_is_refused_and_changes_nothing(
    shop: Vendor, bands: list[dict[str, Any]]
) -> None:
    """A negative cost would pay the buyer to ship; an overlap or inverted range gives two
    prices for one weight. The file is refused with a report and stores nothing."""
    document = export_config(shop)
    _method(document)["bands"] = bands
    before = WeightBand.objects.count()
    audits = AuditLog.objects.count()

    result = import_config(fmt.parse_json(document), vendor=shop, ctx=CTX)

    assert not result.ok
    assert WeightBand.objects.count() == before
    assert AuditLog.objects.count() == audits


@pytest.mark.parametrize("threshold", ["-1", "NaN", "1e30"])
@pytest.mark.django_db
def test_an_import_with_an_absurd_free_shipping_threshold_is_refused(
    shop: Vendor, threshold: str
) -> None:
    document = export_config(shop)
    _method(document)["free_shipping_threshold"] = threshold
    before = list(ShippingMethod.objects.values_list("free_shipping_threshold", flat=True))

    result = import_config(fmt.parse_json(document), vendor=shop, ctx=CTX)

    assert not result.ok
    assert list(ShippingMethod.objects.values_list("free_shipping_threshold", flat=True)) == before


@pytest.mark.django_db
def test_a_vendor_file_cannot_overwrite_another_vendors_zone(shop: Vendor) -> None:
    """The file names a zone code another vendor already uses: the import works on the
    importing vendor's own zones only, and the other vendor's zone is left as it was."""
    other = VendorFactory.create()
    theirs = ShippingZoneFactory.create(vendor=other, code="home", name="Theirs", countries=["DE"])
    document = export_config(shop)
    document["zones"][0]["name"] = "Hijacked"
    document["zones"][0]["countries"] = ["US"]

    import_config(fmt.parse_json(document), vendor=shop, ctx=CTX)

    theirs.refresh_from_db()
    assert (theirs.name, theirs.countries) == ("Theirs", ["DE"])
