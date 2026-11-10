"""ORD.2: the database enforces what the models promise (triggers and constraints).

Snapshots are immutable, states move only along the engine's arrows, and the totals add up,
whatever the client: `QuerySet.update()`, `bulk_update` and raw SQL all fail.
"""

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from django.db import IntegrityError, connection, transaction

from apps.catalog.tests.factories import ProductFactory
from apps.orders import engine
from apps.orders.models import Order, OrderLine, VendorOrder
from apps.orders.tests.factories import (
    OrderFactory,
    OrderLineFactory,
    VendorOrderFactory,
    unchecked,
)

pytestmark = pytest.mark.django_db


def refuses(sql: str, params: list[Any] | None = None) -> None:
    with pytest.raises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(sql, params)


def force_state(table: str, pk: int, state: str) -> None:
    """Put a row in any state, skipping the triggers (test set-up only)."""
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL session_replication_role = replica")
        cursor.execute(f"UPDATE {table} SET state = %s WHERE id = %s", [state, pk])  # noqa: S608
        cursor.execute("SET LOCAL session_replication_role = origin")


# --- immutable lines ------------------------------------------------------------------------


def test_a_line_cannot_be_changed_by_queryset_update() -> None:
    line = OrderLineFactory.create()
    with pytest.raises(IntegrityError), transaction.atomic():
        OrderLine.objects.filter(pk=line.pk).update(title="Other")
    with pytest.raises(IntegrityError), transaction.atomic():
        OrderLine.objects.filter(pk=line.pk).update(unit_price_net=Decimal("1.00"))


def test_a_line_cannot_be_changed_by_bulk_update_or_raw_sql() -> None:
    line = OrderLineFactory.create()
    line.quantity = 5
    with pytest.raises(IntegrityError), transaction.atomic():
        OrderLine.objects.bulk_update([line], ["quantity"])
    refuses("UPDATE orders_orderline SET sku = 'X' WHERE id = %s", [line.pk])


def test_a_line_cannot_be_deleted() -> None:
    line = OrderLine.objects.get(pk=OrderLineFactory.create().pk)
    with pytest.raises(PermissionError):
        line.delete()
    with pytest.raises(IntegrityError), transaction.atomic():
        OrderLine.objects.filter(pk=line.pk).delete()
    refuses("DELETE FROM orders_orderline WHERE id = %s", [line.pk])


def test_the_only_allowed_line_change_is_clearing_the_product() -> None:
    product = ProductFactory.create()
    line = OrderLineFactory.create(product=product)
    with pytest.raises(IntegrityError), transaction.atomic():
        OrderLine.objects.filter(pk=line.pk).update(product=ProductFactory.create())
    OrderLine.objects.filter(pk=line.pk).update(product=None)
    assert OrderLine.objects.get(pk=line.pk).product is None


# --- protected totals and identity of orders and sub-orders ---------------------------------


@pytest.mark.parametrize("column", ["goods_net", "shipping_net", "tax_total", "grand_total"])
def test_order_money_columns_cannot_change(column: str) -> None:
    order = OrderFactory.create()
    with pytest.raises(IntegrityError), transaction.atomic():
        Order.objects.filter(pk=order.pk).update(**{column: Decimal("1.00")})


def test_the_order_number_cannot_change_but_personal_data_can_be_scrubbed() -> None:
    order = OrderFactory.create()
    with pytest.raises(IntegrityError), transaction.atomic():
        Order.objects.filter(pk=order.pk).update(number="2999-999999")
    Order.objects.filter(pk=order.pk).update(email="", billing_address={}, shipping_address={})
    assert Order.objects.get(pk=order.pk).email == ""


@pytest.mark.parametrize(
    "column", ["goods_net", "tax_total", "gross_total", "shipping_tax_amount", "shipping_tax_rate"]
)
def test_vendor_order_money_columns_cannot_change(column: str) -> None:
    sub = VendorOrderFactory.create()
    with pytest.raises(IntegrityError), transaction.atomic():
        VendorOrder.objects.filter(pk=sub.pk).update(**{column: Decimal("1.00")})


def test_vendor_order_snapshots_and_identity_cannot_change() -> None:
    sub = VendorOrderFactory.create(commission_snapshot={"rule_id": "x"})
    other = VendorOrderFactory.create()
    for change in (
        {"commission_snapshot": {"rule_id": "y"}},
        {"vendor_snapshot": {"display_name": "Y"}},
        {"tax_profile_snapshot": {"a": 1}},
        {"shipping_choice": {"a": 1}},
        {"order": other.order},
        {"vendor": other.vendor},
    ):
        with pytest.raises(IntegrityError), transaction.atomic():
            VendorOrder.objects.filter(pk=sub.pk).update(**change)


def test_tracking_and_dates_can_still_change() -> None:
    sub = VendorOrderFactory.create()
    VendorOrder.objects.filter(pk=sub.pk).update(tracking_number="T1", carrier="C")
    assert VendorOrder.objects.get(pk=sub.pk).tracking_number == "T1"


# --- state transitions in the database ------------------------------------------------------


def test_an_order_cannot_be_inserted_in_a_non_initial_state() -> None:
    # bulk_create skips save(), so this reaches the database trigger.
    with pytest.raises(IntegrityError), transaction.atomic():
        Order.objects.bulk_create([Order(**{**_order_kwargs(), "state": "paid"})])
    sub = VendorOrderFactory.create()
    other = OrderFactory.create()
    with pytest.raises(IntegrityError), transaction.atomic():
        VendorOrder.objects.bulk_create(
            [VendorOrder(order=other, vendor=sub.vendor, state="shipped")]
        )
    Order.objects.bulk_create([Order(**{**_order_kwargs(), "state": "awaiting_payment"})])


def test_the_save_guard_also_refuses_a_non_initial_new_order() -> None:
    with pytest.raises(engine.IllegalTransition):
        Order(**{**_order_kwargs(), "state": "paid"}).save()


def _order_kwargs() -> dict[str, object]:
    order = OrderFactory.build()
    return {
        "number": "2998-000001",
        "email": order.email,
        "billing_country": "IT",
        "shipping_country": "IT",
        "goods_net": 0,
        "shipping_net": 0,
        "tax_total": 0,
        "grand_total": 0,
    }


def test_queryset_update_cannot_make_an_illegal_transition() -> None:
    order = OrderFactory.create()
    with pytest.raises(IntegrityError), transaction.atomic():
        Order.objects.filter(pk=order.pk).update(state="paid")
    sub = VendorOrderFactory.create()
    with pytest.raises(IntegrityError), transaction.atomic():
        VendorOrder.objects.filter(pk=sub.pk).update(state="delivered")
    refuses("UPDATE orders_order SET state = 'closed' WHERE id = %s", [order.pk])


def test_save_with_update_fields_cannot_skip_the_guard_in_the_database() -> None:
    order = OrderFactory.create()
    Order.objects.filter(pk=order.pk).update(state="awaiting_payment")
    deferred = Order.objects.only("number").get(pk=order.pk)
    deferred.state = "closed"
    with pytest.raises(IntegrityError), transaction.atomic():
        deferred.save()


@pytest.mark.parametrize(
    ("source", "target"),
    [(s, t) for s in engine.ORDER_STATES for t in engine.ORDER_STATES if s != t],
)
def test_the_database_allows_exactly_the_engine_order_transitions(source: str, target: str) -> None:
    order = OrderFactory.create()
    force_state("orders_order", order.pk, source)
    legal = target in engine.ORDER_TRANSITIONS[source]
    if legal:
        Order.objects.filter(pk=order.pk).update(state=target)
        assert Order.objects.get(pk=order.pk).state == target
    else:
        with pytest.raises(IntegrityError), transaction.atomic():
            Order.objects.filter(pk=order.pk).update(state=target)


@pytest.mark.parametrize(
    ("source", "target"),
    [(s, t) for s in engine.VENDOR_ORDER_STATES for t in engine.VENDOR_ORDER_STATES if s != t],
)
def test_the_database_allows_exactly_the_engine_vendor_order_transitions(
    source: str, target: str
) -> None:
    sub = VendorOrderFactory.create()
    force_state("orders_vendororder", sub.pk, source)
    if target in engine.VENDOR_ORDER_TRANSITIONS[source]:
        VendorOrder.objects.filter(pk=sub.pk).update(state=target)
        assert VendorOrder.objects.get(pk=sub.pk).state == target
    else:
        with pytest.raises(IntegrityError), transaction.atomic():
            VendorOrder.objects.filter(pk=sub.pk).update(state=target)


# --- row-local money checks on lines --------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"unit_price_net": Decimal("0.00"), "net_total": Decimal("0.00"), "tax_amount": 0},
        {"net_total": Decimal("10.01")},  # 10.00 x 1 - 0
        {"tax_amount": Decimal("2.21")},  # 22% of 10.00 is 2.20
        {"unit_price_net": Decimal("-1.00"), "net_total": Decimal("-1.00"), "tax_amount": 0},
        {"quantity": 2, "discount_net": Decimal("5.00")},  # net must be 15.00
    ],
)
def test_a_line_must_do_its_own_sums(overrides: dict[str, object]) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        OrderLineFactory.create(**overrides)


def test_a_line_rounds_half_up() -> None:
    # 0.25 x 22% = 0.055 -> 0.06
    with unchecked():
        OrderLineFactory.create(
            unit_price_net=Decimal("0.25"), net_total=Decimal("0.25"), tax_amount=Decimal("0.06")
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        OrderLineFactory.create(
            unit_price_net=Decimal("0.25"), net_total=Decimal("0.25"), tax_amount=Decimal("0.05")
        )


def test_negative_totals_are_refused() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        OrderFactory.create(
            goods_net=Decimal("-1.00"), tax_total=Decimal("0"), grand_total=Decimal("-1.00")
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        VendorOrderFactory.create(
            goods_net=Decimal("-1.00"), tax_total=Decimal("0"), gross_total=Decimal("-1.00")
        )


# --- cross-table sums, checked at commit ----------------------------------------------------


def check_now() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def fails_at_commit(build: Callable[[], None]) -> None:
    """`build` makes rows that break a sum; the check fails and the savepoint drops them."""
    try:
        with transaction.atomic():
            build()
            check_now()
    except IntegrityError:
        return
    pytest.fail("the commit-time check accepted inconsistent totals")


def test_a_line_the_sub_order_does_not_know_fails_at_commit() -> None:
    sub = VendorOrderFactory.create()  # no lines: goods 0.00, consistent
    check_now()

    def build() -> None:
        OrderLineFactory.create(vendor_order=sub)

    fails_at_commit(build)


def test_a_consistent_order_passes_the_commit_check() -> None:
    OrderLineFactory.create()
    check_now()


def test_an_order_that_differs_from_its_sub_orders_fails_at_commit() -> None:
    def build() -> None:
        order = OrderFactory.create(
            goods_net=Decimal("10.00"), tax_total=Decimal("0"), grand_total=Decimal("10.00")
        )
        VendorOrderFactory.create(order=order)  # sub-order totals are 0.00

    fails_at_commit(build)


def test_shipping_tax_is_part_of_the_sub_order_tax() -> None:
    order = OrderFactory.create(
        goods_net=Decimal("10.00"),
        shipping_net=Decimal("5.00"),
        tax_total=Decimal("3.30"),
        grand_total=Decimal("18.30"),
    )
    sub = VendorOrderFactory.create(
        order=order,
        goods_net=Decimal("10.00"),
        shipping_net=Decimal("5.00"),
        shipping_tax_rate=Decimal("22"),
        shipping_tax_amount=Decimal("1.10"),
        tax_total=Decimal("3.30"),
        gross_total=Decimal("18.30"),
    )
    OrderLineFactory.create(vendor_order=sub)
    check_now()
