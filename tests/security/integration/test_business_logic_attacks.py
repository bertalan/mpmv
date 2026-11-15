"""Attack scenarios, business logic (SEC.5, owner security wave item 10).

Each test plays the attacker against our own code and asserts that the attack fails and leaves
nothing behind. The suite lives under `integration/` because the root conftest files tests by
directory (ADR-0006) and several cases need real PostgreSQL (concurrent transactions).

Cases already covered elsewhere are not repeated here:

- more units than the stock, in one add or across adds: `apps/cart/tests/integration/
  test_services.py::test_adding_more_than_the_stock_is_refused` and `::test_a_variant_line_uses_
  the_variant_price_and_stock`;
- quantity 0 or below in the pure rule: `apps/cart/tests/unit/test_engine.py::test_quantity_
  error_explains_what_is_wrong`; the per-line ceiling: `::test_the_line_limit_is_the_stock_capped_
  by_the_per_line_maximum`;
- a double-submitted sign-in merging the guest cart twice: `apps/cart/tests/integration/
  test_services.py::test_merging_a_guest_cart_that_was_already_adopted_is_a_no_op` and
  `::test_merging_a_guest_cart_that_was_already_merged_is_a_no_op`;
- a stale display price in the cart is flagged, never charged: `apps/cart/tests/integration/
  test_services.py::test_packages_show_current_prices_and_flag_changes`.

Checkout, orders, payments, refunds, payouts and coupons do not exist yet on main. Their cases
are skipped placeholders that name the backlog task and spell out the assertion to implement, so
the suite records the obligation: un-skip each one in the task that builds the code.
"""

import threading
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import Client

from apps.api.tests.factories import ApiClientFactory
from apps.cart import engine as cart_engine
from apps.cart import services as cart
from apps.cart.models import Cart, CartLine
from apps.cart.tests.factories import CartFactory
from apps.catalog.models import Product, Variant
from apps.catalog.tests.factories import ProductFactory, VariantFactory
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

PRODUCTS_API = "/api/v1/catalog/products"


def sellable(**fields: Any) -> Product:
    """A published `buy` product of an active vendor, 10 in stock, 10.00 net."""
    values: dict[str, Any] = {
        "vendor": VendorFactory.create(status=Vendor.Status.ACTIVE),
        "status": Product.Status.PUBLISHED,
        "stock_qty": 10,
        "price_net": Decimal("10.00"),
        **fields,
    }
    return ProductFactory.create(**values)


def guest_cart(key: str = "attacker-session") -> Cart:
    return CartFactory.create(session_key=key)


# --- A price or VAT sent by the client --------------------------------------------------------


@pytest.mark.parametrize("smuggled", ["price", "price_net", "unit_price", "vat_rate", "tax"])
@pytest.mark.django_db
def test_the_cart_service_takes_no_price_or_vat_from_the_caller(smuggled: str) -> None:
    """There is no channel for a client price: the service signature refuses one outright."""
    product = sellable()
    add: Callable[..., Any] = cart.add_to_cart
    with pytest.raises(TypeError):
        add(guest_cart(), product, quantity=1, **{smuggled: Decimal("0.01")})
    assert not CartLine.objects.exists()


@pytest.mark.django_db
def test_a_tampered_display_price_in_the_cart_is_never_the_price() -> None:
    """Even a line whose stored display price was rewritten (a forged row, a stale cache) is
    priced from the catalogue, and the difference is flagged to the customer."""
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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("price_net", "-10.00"),
        ("price_net", "0"),
        ("price_net", "0.001"),
        ("price_net", "NaN"),
        ("price_net", "Infinity"),
        ("price_net", "1e30"),
        ("fiscal_category", "zero_rated"),
    ],
)
@pytest.mark.django_db
def test_the_api_refuses_absurd_prices_and_unknown_tax_categories(
    client: Client, field: str, value: str
) -> None:
    """A vendor key cannot create a product at a negative, zero, non-finite or overflowing
    price, nor pick a tax treatment outside the fiscal categories: a 4xx problem, never a 500
    and never a row."""
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    _, key = ApiClientFactory.create_with_key(
        vendor=vendor, scopes=["catalog:read", "catalog:write"]
    )
    payload = {
        "sku": "ATK-1",
        "price_net": "19.90",
        "translations": [{"language": "en", "title": "Probe"}],
        field: value,
    }
    response = client.post(
        PRODUCTS_API,
        payload,
        content_type="application/json",
        headers={"Authorization": f"Bearer {key}"},
    )
    assert 400 <= response.status_code < 500, response.content
    assert not Product.objects.filter(sku="ATK-1").exists()


@pytest.mark.parametrize(
    ("variant_field", "value"),
    [("price_net_override", "-5.00"), ("price_net_override", "0"), ("stock_qty", 10**12)],
)
@pytest.mark.django_db
def test_the_api_refuses_absurd_variant_prices_and_stock(
    client: Client, variant_field: str, value: object
) -> None:
    from apps.catalog.tests.factories import clothing_attributes

    clothing_attributes()
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    _, key = ApiClientFactory.create_with_key(
        vendor=vendor, scopes=["catalog:read", "catalog:write"]
    )
    variant: dict[str, Any] = {"sku": "ATK-2-S", "options": {"size": "s"}, variant_field: value}
    payload = {
        "sku": "ATK-2",
        "kind": "variable",
        "price_net": "19.90",
        "translations": [{"language": "en", "title": "Probe"}],
        "attributes": [{"attribute": "size", "values": ["s"], "variant_axis": True}],
        "variants": [variant],
    }
    response = client.post(
        PRODUCTS_API,
        payload,
        content_type="application/json",
        headers={"Authorization": f"Bearer {key}"},
    )
    assert 400 <= response.status_code < 500, response.content
    assert not Product.objects.filter(sku="ATK-2").exists()
    assert not Variant.objects.filter(sku="ATK-2-S").exists()


@pytest.mark.skip(reason="needs ORD.4")
def test_place_order_ignores_a_client_price_and_vat_and_recomputes_them() -> None:
    """To implement in ORD.4: `place_order` called with a cart whose lines carry a forged
    `added_price_net` (0.01) and a request body carrying `price`, `vat_rate` and `total` fields
    snapshots the catalogue net price and the tax engine's rate (`resolve_goods_tax`), never the
    client's; the order total equals the server-side recomputation to the cent."""


@pytest.mark.skip(reason="needs ORD.3")
def test_a_shipping_or_tax_quote_ignores_client_amounts() -> None:
    """To implement in ORD.3: `build_checkout` takes the cart and the address only; a request
    that adds `shipping_net`, `vat_rate` or `goods_net` gets the same preview as one without."""


# --- Negative, zero or huge quantities --------------------------------------------------------


@pytest.mark.parametrize("quantity", [0, -1, -(2**31), -(10**30)])
@pytest.mark.django_db
def test_adding_zero_or_negative_units_is_refused(quantity: int) -> None:
    basket = guest_cart()
    with pytest.raises(ValidationError):
        cart.add_to_cart(basket, sellable(), quantity=quantity)
    assert not CartLine.objects.exists()


@pytest.mark.parametrize("quantity", [cart_engine.MAX_LINE_QUANTITY + 1, 2**31, 2**63, 10**30])
@pytest.mark.django_db
def test_huge_quantities_are_refused_before_the_database(quantity: int) -> None:
    """Above the per-line ceiling the service refuses with a message, even when the product
    has unlimited backorders: no integer overflow, no 500."""
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
    """Setting a negative quantity is read as "remove": no line with a negative count (a
    negative line would turn into a credit at checkout)."""
    basket = guest_cart()
    cart.add_to_cart(basket, sellable(), quantity=2)
    cart.set_line_quantity(CartLine.objects.get(cart=basket), quantity)
    assert not CartLine.objects.filter(cart=basket).exists()


@pytest.mark.parametrize("quantity", [1.5, Decimal("1.5"), Decimal("2"), 2.0, True])
@pytest.mark.django_db
def test_fractional_and_non_integer_quantities_are_refused(quantity: Any) -> None:
    """A quantity is a whole number of units: 1.5 must not be truncated to 1 (or rounded to 2)
    by the database driver, and a boolean is not a count."""
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


# --- Coupon or discount stacking --------------------------------------------------------------


@pytest.mark.skip(reason="needs ORD.12")
def test_a_coupon_cannot_be_applied_twice_or_stacked_past_its_rule() -> None:
    """To implement in ORD.12: applying the same coupon code twice (sequentially and in two
    concurrent requests) gives one gift line; two codes whose rules exclude each other give the
    first only; no line is ever priced below 0.00 and the order total never goes negative."""


@pytest.mark.skip(reason="needs CAT.17")
def test_a_bundle_discount_does_not_stack_with_a_line_discount() -> None:
    """To implement in CAT.17: a bundle's discounted line price is the only discount on its
    components; a coupon on a component does not reduce it again below the funded share."""


# --- A currency or country switch in the middle of checkout -----------------------------------


@pytest.mark.skip(reason="needs ORD.4")
def test_a_country_switch_after_the_preview_reprices_or_refuses_the_order() -> None:
    """To implement in ORD.4: a checkout previewed for one destination (tax and shipping of
    country A) and submitted with country B recomputes tax and shipping for B before placing
    the order, and refuses (no order, no payment intent) when the confirmed total differs from
    the total the customer accepted."""


@pytest.mark.skip(reason="needs PAY.1")
def test_the_payment_amount_and_currency_come_from_the_order_snapshot() -> None:
    """To implement in PAY.1: the PaymentIntent is created from the order snapshot's total in
    EUR; a request carrying `currency` or `amount` changes neither."""


# --- A double submit of payment or order ------------------------------------------------------


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
    """Two "add 2" requests for the same cart at once, with 3 in stock: the cart lock makes
    them run one after the other, so one is refused and the line holds 2, never 4."""
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


@pytest.mark.skip(reason="needs ORD.4")
def test_a_double_submitted_order_places_one_order() -> None:
    """To implement in ORD.4: two concurrent `place_order` calls for the same cart and
    idempotency key give one Order (the second returns it or is refused), one set of stock
    reservations and one AuditLog entry."""


@pytest.mark.skip(reason="needs PAY.1")
def test_a_double_submitted_payment_creates_one_payment_intent() -> None:
    """To implement in PAY.1: two concurrent "pay" submits for one order send the gateway the
    same idempotency key and record one Payment; the fake gateway sees one charge."""


# --- A stock race between two buyers ----------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_two_buyers_may_both_hold_the_last_unit_in_their_carts() -> None:
    """By design the cart does not reserve stock (ORD.4's StockReservation does): both carts
    take the last unit without error, and the stock itself is untouched. The real race is at
    order placement, below."""
    product = sellable(stock_qty=1)
    first, second = guest_cart("buyer-1"), guest_cart("buyer-2")
    outcomes = _race(
        lambda: cart.add_to_cart(first, product, quantity=1),
        lambda: cart.add_to_cart(second, product, quantity=1),
    )
    assert not any(isinstance(outcome, Exception) for outcome in outcomes), outcomes
    product.refresh_from_db()
    assert product.stock_qty == 1


@pytest.mark.skip(reason="needs ORD.4")
def test_two_buyers_racing_for_the_last_unit_get_one_order() -> None:
    """To implement in ORD.4 (its done-when): stock 1, two carts each holding it, two
    `place_order` calls in concurrent transactions on real PostgreSQL: exactly one order is
    placed, the other is refused with "out of stock", one StockReservation exists and the stock
    never goes below 0."""


# --- Refunds over the amount paid -------------------------------------------------------------


@pytest.mark.skip(reason="needs PAY.4")
def test_refunds_cannot_exceed_the_amount_paid() -> None:
    """To implement in PAY.4: on an order paid 100.00, a refund of 100.01 is refused; two
    partial refunds of 60.00 (sequential, then concurrent) give one refund and a refusal; the
    ledger reverses VAT and commission once per refund, and every refund writes an AuditLog
    entry with the actor."""


@pytest.mark.skip(reason="needs PAY.2")
def test_a_replayed_refund_webhook_does_not_refund_twice() -> None:
    """To implement in PAY.2: a `charge.refunded` event delivered twice has the effect of one
    (one ledger reversal, one status change)."""


# --- Payouts to a changed IBAN ----------------------------------------------------------------


@pytest.mark.skip(reason="needs VEN.7")
def test_a_payout_right_after_an_iban_change_is_held() -> None:
    """To implement in VEN.7 (with SEC.3): changing a vendor's IBAN writes an AuditLog entry,
    notifies the vendor's legal email at the old and the new address, and holds payouts to the
    new IBAN for the cooling-off period; `assert_payout_allowed` refuses a payout requested in
    that period, and a staff member cannot change an IBAN and approve the payout alone."""
