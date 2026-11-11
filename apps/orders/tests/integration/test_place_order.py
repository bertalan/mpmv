"""ORD.4: `place_order` verifies prices and stock inside one transaction and reserves stock.

Done-when: concurrency test on stock (two concurrent placements of the last unit: exactly one
succeeds). Also: price verification (a changed price refuses the order, never reprices), stock
reservation (other carts' holds count, expired holds do not), snapshots (nature code from the tax
decision, never from the caller), dry run and audit.
"""

import dataclasses
import datetime as dt
import threading
from decimal import Decimal
from typing import Any

import pytest
from django.db import connection

from apps.cart.tests.factories import CartFactory
from apps.catalog.models import Product
from apps.catalog.tests.factories import ProductFactory, VariantFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.orders import services
from apps.orders.models import Order, OrderLine, StockReservation
from apps.orders.services import (
    CheckoutPreview,
    OrderRefused,
    PlaceLine,
    PlaceVendor,
    hold_stock,
    place_order,
)
from apps.orders.tests.factories import StockReservationFactory
from apps.tax.engine import NATURE_BY_REASON, TaxDecision
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

CTX = AuditContext(actor_label="system:test", source=AuditLog.Source.SYSTEM)
NOW = dt.datetime(2027, 3, 1, 10, 0, tzinfo=dt.UTC)
COMMISSION = {
    "rule_id": "default",
    "version": 1,
    "percent": "10.00",
    "base": "goods",
    "fee_percent": "1.40",
    "fee_fixed": "0.25",
    "fee_base": "gross",
}


@dataclasses.dataclass(frozen=True)
class FakeTax:
    """The shape of a TaxDecision (TAX.3): what the order reads, never what a caller types."""

    rate: Decimal = Decimal("22")
    reason: str = "domestic"
    legal_note_key: str | None = None
    nature_code: str | None = None


def sellable(vendor: Vendor | None = None, **fields: Any) -> Product:
    values: dict[str, Any] = {
        "vendor": vendor or VendorFactory.create(status=Vendor.Status.ACTIVE),
        "status": Product.Status.PUBLISHED,
        "stock_qty": 10,
        "price_net": Decimal("10.00"),
        **fields,
    }
    return ProductFactory.create(**values)


def line(product: Product, quantity: int = 1, **overrides: Any) -> PlaceLine:
    values: dict[str, Any] = {
        "product": product,
        "variant": None,
        "quantity": quantity,
        "title": "Ceramic cup",
        "expected_unit_price_net": product.price_net,
        "tax": FakeTax(),
    }
    values.update(overrides)
    return PlaceLine(**values)


def preview(*vendors: PlaceVendor, **overrides: Any) -> CheckoutPreview:
    values: dict[str, Any] = {
        "email": "buyer@example.com",
        "language": "en",
        "billing_address": {"line1": "1 Via Test", "city": "Roma"},
        "billing_country": "IT",
        "shipping_address": {"line1": "1 Via Test", "city": "Roma"},
        "shipping_country": "IT",
        "vendors": list(vendors),
    }
    values.update(overrides)
    return CheckoutPreview(**values)


def vendor_of(product: Product, *lines: PlaceLine, **overrides: Any) -> PlaceVendor:
    values: dict[str, Any] = {
        "vendor": product.vendor,
        "lines": list(lines),
        "shipping_net": Decimal("5.00"),
        "shipping_tax": FakeTax(),
        "commission_snapshot": COMMISSION,
    }
    values.update(overrides)
    return PlaceVendor(**values)


def place(product: Product, quantity: int = 1, **overrides: Any) -> Order:
    order, _ = place_order(
        preview(vendor_of(product, line(product, quantity)), **overrides), ctx=CTX, now=NOW
    )
    assert order is not None
    return order


# --- the happy path ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_placed_order_awaits_payment_with_snapshots_and_totals() -> None:
    product = sellable()
    order = place(product, 2)
    assert order.state == "awaiting_payment"
    only = OrderLine.objects.get(vendor_order__order=order)
    assert (only.unit_price_net, only.quantity, only.net_total, only.tax_amount) == (
        Decimal("10.00"),
        2,
        Decimal("20.00"),
        Decimal("4.40"),
    )
    assert only.sku == product.sku
    assert order.grand_total == Decimal("20.00") + Decimal("4.40") + Decimal("5.00") + Decimal(
        "1.10"
    )


@pytest.mark.django_db
def test_a_variant_override_price_is_the_live_price() -> None:
    variable = ProductFactory.create(
        vendor=VendorFactory.create(status=Vendor.Status.ACTIVE),
        kind=Product.Kind.VARIABLE,
        status=Product.Status.PUBLISHED,
        price_net=Decimal("10.00"),
    )
    variant = VariantFactory.create(
        product=variable, stock_qty=3, price_net_override=Decimal("12.50")
    )
    order, _ = place_order(
        preview(
            vendor_of(
                variable,
                line(variable, variant=variant, expected_unit_price_net=Decimal("12.50")),
            )
        ),
        ctx=CTX,
        now=NOW,
    )
    assert order is not None
    assert OrderLine.objects.get(vendor_order__order=order).unit_price_net == Decimal("12.50")
    assert StockReservation.objects.get(order=order).variant == variant


# --- price verification -----------------------------------------------------------------------


@pytest.mark.django_db
def test_a_changed_price_refuses_the_order_and_never_reprices() -> None:
    product = sellable()
    stale = line(product, expected_unit_price_net=Decimal("9.00"))
    with pytest.raises(OrderRefused) as refused:
        place_order(preview(vendor_of(product, stale)), ctx=CTX, now=NOW)
    (reason,) = refused.value.reasons
    assert reason.code == "price_changed"
    assert reason.product_id == product.pk
    assert "9.00" in reason.detail
    assert "10.00" in reason.detail
    assert Order.objects.count() == 0
    assert StockReservation.objects.count() == 0
    assert AuditLog.objects.filter(action="orders.create").count() == 0


@pytest.mark.django_db
def test_a_product_that_cannot_be_bought_any_more_is_refused() -> None:
    product = sellable(status=Product.Status.DRAFT)
    with pytest.raises(OrderRefused) as refused:
        place_order(preview(vendor_of(product, line(product))), ctx=CTX, now=NOW)
    assert [r.code for r in refused.value.reasons] == ["unavailable"]


# --- stock ------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_not_enough_stock_refuses_with_the_available_quantity() -> None:
    product = sellable(stock_qty=2)
    with pytest.raises(OrderRefused) as refused:
        place_order(preview(vendor_of(product, line(product, 3))), ctx=CTX, now=NOW)
    (reason,) = refused.value.reasons
    assert reason.code == "out_of_stock"
    assert "2" in reason.detail
    assert Order.objects.count() == 0


@pytest.mark.django_db
def test_all_reasons_are_reported_and_nothing_persists_when_one_vendor_fails() -> None:
    good, short = sellable(), sellable(stock_qty=0)
    with pytest.raises(OrderRefused) as refused:
        place_order(
            preview(vendor_of(good, line(good)), vendor_of(short, line(short))), ctx=CTX, now=NOW
        )
    assert [r.code for r in refused.value.reasons] == ["out_of_stock"]
    assert Order.objects.count() == 0
    assert StockReservation.objects.count() == 0


@pytest.mark.django_db
def test_placing_confirms_a_hold_without_decrementing_stock() -> None:
    product = sellable(stock_qty=5)
    order = place(product, 2)
    hold = StockReservation.objects.get(order=order)
    assert (hold.product, hold.quantity) == (product, 2)
    assert hold.expires_at > NOW
    product.refresh_from_db()
    assert product.stock_qty == 5  # decremented when the payment succeeds (on_payment_succeeded)


@pytest.mark.django_db
def test_another_carts_active_hold_reduces_what_can_be_ordered() -> None:
    product = sellable(stock_qty=3)
    StockReservation.objects.create(
        product=product,
        cart=CartFactory.create(),
        quantity=2,
        expires_at=NOW + dt.timedelta(minutes=5),
    )
    with pytest.raises(OrderRefused) as refused:
        place_order(preview(vendor_of(product, line(product, 2))), ctx=CTX, now=NOW)
    assert refused.value.reasons[0].code == "out_of_stock"
    assert place(product, 1).state == "awaiting_payment"


@pytest.mark.django_db
def test_an_expired_hold_is_ignored() -> None:
    product = sellable(stock_qty=1)
    StockReservationFactory.create(
        product=product, cart=CartFactory.create(), expires_at=NOW - dt.timedelta(seconds=1)
    )
    assert place(product, 1).state == "awaiting_payment"


@pytest.mark.django_db
def test_the_carts_own_hold_is_not_counted_twice_and_becomes_the_orders() -> None:
    product = sellable(stock_qty=1)
    cart = CartFactory.create()
    StockReservation.objects.create(
        product=product, cart=cart, quantity=1, expires_at=NOW + dt.timedelta(minutes=5)
    )
    order, _ = place_order(preview(vendor_of(product, line(product)), cart=cart), ctx=CTX, now=NOW)
    assert order is not None
    (hold,) = StockReservation.objects.all()
    assert hold.order == order
    assert hold.cart is None


@pytest.mark.django_db
def test_an_always_open_product_needs_no_hold() -> None:
    product = sellable(stock_policy=Product.StockPolicy.ALWAYS_OPEN, stock_qty=0)
    order = place(product, 4)
    assert order.state == "awaiting_payment"
    assert StockReservation.objects.count() == 0


# --- dry run and audit ------------------------------------------------------------------------


@pytest.mark.django_db
def test_dry_run_changes_nothing_but_still_refuses() -> None:
    product = sellable()
    order, changeset = place_order(
        preview(vendor_of(product, line(product))), ctx=CTX, now=NOW, dry_run=True
    )
    assert order is None
    assert changeset
    assert Order.objects.count() == 0
    assert StockReservation.objects.count() == 0
    with pytest.raises(OrderRefused):
        place_order(preview(vendor_of(product, line(product, 99))), ctx=CTX, now=NOW, dry_run=True)


@pytest.mark.django_db
def test_placing_writes_the_audit_trail() -> None:
    order = place(sellable())
    assert AuditLog.objects.filter(action="orders.create").exists()
    assert order.pk


# --- nature code is read from the tax decision ------------------------------------------------


@pytest.mark.django_db
def test_the_nature_code_comes_from_the_tax_decision() -> None:
    product = sellable()
    exempt = FakeTax(rate=Decimal("0"), reason="eu_b2b_reverse_charge", nature_code="N6.9")
    order, _ = place_order(preview(vendor_of(product, line(product, tax=exempt))), ctx=CTX, now=NOW)
    assert order is not None
    assert OrderLine.objects.get(vendor_order__order=order).vat_nature_code == "N6.9"
    taxed = place(sellable())
    assert OrderLine.objects.get(vendor_order__order=taxed).vat_nature_code == ""


def test_a_caller_cannot_feed_the_nature_code() -> None:
    assert "vat_nature_code" not in {f.name for f in dataclasses.fields(PlaceLine)}


@pytest.mark.django_db
def test_a_real_tax_decision_feeds_the_nature_code() -> None:
    decision = TaxDecision(
        rate=Decimal("0"),
        reason="reverse_charge",
        country_of_taxation=None,
        legal_note_key=None,
        nature_code=NATURE_BY_REASON["reverse_charge"],
    )
    product = sellable()
    order, _ = place_order(
        preview(vendor_of(product, line(product, tax=decision))), ctx=CTX, now=NOW
    )
    assert order is not None
    assert OrderLine.objects.get(vendor_order__order=order).vat_nature_code == "N3.2"


# --- concurrency (the row's Done-when) --------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_two_concurrent_orders_for_the_last_unit_one_wins() -> None:
    product = sellable(stock_qty=1)
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            wanted = preview(vendor_of(product, line(product)))
            barrier.wait(timeout=10)
            try:
                place_order(wanted, ctx=CTX)
                outcomes.append("placed")
            except OrderRefused as refused:
                outcomes.append(refused.reasons[0].code)
        except BaseException as exc:
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not any(thread.is_alive() for thread in threads)
    assert not errors
    assert sorted(outcomes) == ["out_of_stock", "placed"]
    assert Order.objects.count() == 1
    assert StockReservation.objects.filter(product=product).count() == 1
    assert services.peek_order_number()  # the loser's number went back to the pool


@pytest.mark.django_db
def test_a_cart_hold_counts_against_other_carts_and_replaces_its_own() -> None:
    product = sellable(stock_qty=2)
    mine, other = CartFactory.create(), CartFactory.create()
    hold_stock(mine, product, None, 2, now=NOW)
    hold_stock(mine, product, None, 1, now=NOW)  # replaces, not adds
    assert StockReservation.objects.get(cart=mine).quantity == 1
    with pytest.raises(OrderRefused) as refused:
        hold_stock(other, product, None, 2, now=NOW)
    assert refused.value.reasons[0].code == "out_of_stock"
    hold_stock(other, product, None, 1, now=NOW)


@pytest.mark.django_db(transaction=True)
def test_a_cart_hold_and_an_order_race_for_the_last_unit_one_wins() -> None:
    product = sellable(stock_qty=1)
    cart = CartFactory.create()
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    errors: list[BaseException] = []

    def attempt(action: Any) -> None:
        try:
            barrier.wait(timeout=10)
            try:
                action()
                outcomes.append("ok")
            except OrderRefused as refused:
                outcomes.append(refused.reasons[0].code)
        except BaseException as exc:
            errors.append(exc)
        finally:
            connection.close()

    wanted = preview(vendor_of(product, line(product)))
    threads = [
        threading.Thread(target=attempt, args=(lambda: hold_stock(cart, product, None, 1),)),
        threading.Thread(target=attempt, args=(lambda: place_order(wanted, ctx=CTX),)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not any(thread.is_alive() for thread in threads)
    assert not errors
    assert sorted(outcomes) == ["ok", "out_of_stock"]
    assert StockReservation.objects.filter(product=product).count() == 1


@pytest.mark.django_db
def test_the_shipping_tax_comes_from_a_tax_outcome_not_typed_fields() -> None:
    assert not {"shipping_tax_rate", "shipping_tax_reason", "shipping_vat_nature_code"} & {
        f.name for f in dataclasses.fields(PlaceVendor)
    }
    product = sellable()
    exempt = FakeTax(rate=Decimal("0"), reason="export", nature_code="N3.2")
    order, _ = place_order(
        preview(vendor_of(product, line(product), shipping_tax=exempt)), ctx=CTX, now=NOW
    )
    assert order is not None
    sub = order.vendor_orders.get()
    assert (sub.shipping_tax_rate, sub.shipping_tax_reason, sub.shipping_vat_nature_code) == (
        Decimal("0"),
        "export",
        "N3.2",
    )


@pytest.mark.django_db
def test_a_cart_hold_dry_run_checks_the_stock_and_writes_nothing() -> None:
    product = sellable(stock_qty=1)
    cart = CartFactory.create()
    changeset = hold_stock(cart, product, None, 1, now=NOW, dry_run=True)
    assert changeset
    assert StockReservation.objects.count() == 0
    with pytest.raises(OrderRefused):
        hold_stock(cart, product, None, 2, now=NOW, dry_run=True)
