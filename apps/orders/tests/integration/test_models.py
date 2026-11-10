"""ORD.2: the database rules of orders, vendor sub-orders and order lines."""

from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from apps.catalog.tests.factories import ProductFactory
from apps.orders.engine import IllegalTransition
from apps.orders.models import Order, OrderLine, VendorOrder
from apps.orders.tests.factories import (
    OrderFactory,
    OrderLineFactory,
    VendorOrderFactory,
    unchecked,
)

pytestmark = pytest.mark.django_db


def test_a_new_order_starts_in_checkout_and_has_a_public_id() -> None:
    order = OrderFactory.create()
    assert order.state == "checkout"
    assert order.public_id
    assert order.customer is None


def test_the_number_is_unique() -> None:
    order = OrderFactory.create()
    with transaction.atomic(), pytest.raises(IntegrityError):
        OrderFactory.create(number=order.number)


def test_a_vendor_has_one_sub_order_per_order() -> None:
    sub = VendorOrderFactory.create()
    with transaction.atomic(), pytest.raises(IntegrityError):
        VendorOrderFactory.create(order=sub.order, vendor=sub.vendor)


def test_the_grand_total_must_be_net_plus_shipping_plus_tax() -> None:
    with transaction.atomic(), pytest.raises(IntegrityError):
        OrderFactory.create(
            goods_net=Decimal("10.00"),
            shipping_net=Decimal("5.00"),
            tax_total=Decimal("3.30"),
            grand_total=Decimal("18.00"),
        )
    with transaction.atomic(), pytest.raises(IntegrityError):
        VendorOrderFactory.create(
            goods_net=Decimal("10.00"), gross_total=Decimal("11.00"), tax_total=Decimal("0")
        )
    with unchecked():
        OrderFactory.create(
            goods_net=Decimal("10.00"),
            shipping_net=Decimal("5.00"),
            tax_total=Decimal("3.30"),
            grand_total=Decimal("18.30"),
        )


def test_states_outside_the_machine_are_refused_by_the_database() -> None:
    order = OrderFactory.create()
    with transaction.atomic(), pytest.raises(IntegrityError):
        Order.objects.filter(pk=order.pk).update(state="teleported")
    sub = VendorOrderFactory.create()
    with transaction.atomic(), pytest.raises(IntegrityError):
        VendorOrder.objects.filter(pk=sub.pk).update(state="teleported")


def test_an_illegal_order_transition_cannot_be_saved() -> None:
    order = OrderFactory.create()
    order.state = "closed"
    with pytest.raises(IllegalTransition):
        order.save()
    order.refresh_from_db()
    assert order.state == "checkout"


def test_a_legal_order_transition_can_be_saved_and_saving_again_is_fine() -> None:
    order = OrderFactory.create()
    order.state = "awaiting_payment"
    order.save()
    order.save()
    order.refresh_from_db()
    assert order.state == "awaiting_payment"


def test_a_terminal_order_cannot_leave_its_state() -> None:
    order = OrderFactory.create()
    for state in ("awaiting_payment", "cancelled"):
        order.state = state
        order.save()
    order.state = "paid"
    with pytest.raises(IllegalTransition):
        order.save()


def test_an_illegal_vendor_order_transition_cannot_be_saved() -> None:
    sub = VendorOrderFactory.create()
    sub.state = "delivered"
    with pytest.raises(IllegalTransition):
        sub.save()
    sub.state = "accepted"
    sub.save()


def test_a_loaded_order_guards_its_stored_state() -> None:
    order = OrderFactory.create()
    loaded = Order.objects.get(pk=order.pk)
    loaded.state = "paid"
    with pytest.raises(IllegalTransition):
        loaded.save()


def test_a_line_is_an_immutable_snapshot() -> None:
    line = OrderLineFactory.create()
    line.title = "Renamed"
    with pytest.raises(PermissionError):
        line.save()


def test_a_line_survives_its_product_and_keeps_the_sku_label() -> None:
    product = ProductFactory.create(sku="VENDOR-LABEL")
    line = OrderLineFactory.create(product=product, sku=product.sku, title="Kept title")
    line_id = line.pk
    # A product that has been ordered cannot disappear silently, but if it is removed the
    # line keeps its snapshot and only loses the link.
    OrderLine.objects.filter(pk=line_id).update(product=None)
    line = OrderLine.objects.get(pk=line_id)
    assert line.product is None
    assert (line.sku, line.title) == ("VENDOR-LABEL", "Kept title")


def test_line_quantity_and_discount_rules() -> None:
    with transaction.atomic(), pytest.raises(IntegrityError):
        OrderLineFactory.create(quantity=0)
    with transaction.atomic(), pytest.raises(IntegrityError):
        OrderLineFactory.create(discount_net=Decimal("-1.00"))


def test_amounts_are_stored_with_two_places() -> None:
    with unchecked():
        sub = VendorOrderFactory.create(
            goods_net=Decimal("1.50"), gross_total=Decimal("1.50"), tax_total=Decimal("0")
        )
    sub.refresh_from_db()
    assert sub.goods_net == Decimal("1.50")
    assert sub.goods_net.as_tuple().exponent == -2
