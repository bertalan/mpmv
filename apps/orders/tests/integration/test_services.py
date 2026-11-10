"""ORD.2: order creation with snapshots, numbering and audited, dry-run-able transitions."""

import datetime as dt
import threading
from decimal import Decimal

import pytest
from django.db import connection, transaction

from apps.catalog.tests.factories import ProductFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.orders import services
from apps.orders.engine import IllegalTransition
from apps.orders.models import Order, OrderNumberSequence, VendorOrder
from apps.orders.services import LineInput, VendorOrderInput
from apps.vendors.tests.factories import VendorFactory

CTX = AuditContext(actor_label="system:test", source=AuditLog.Source.SYSTEM)
NOW = dt.datetime(2027, 3, 1, 10, 0, tzinfo=dt.UTC)


def line_input(**overrides: object) -> LineInput:
    values: dict[str, object] = {
        "product": None,
        "sku": "VENDOR-SKU-1",
        "title": "Ceramic cup",
        "variant_label": "Blue",
        "unit_price_net": Decimal("10.00"),
        "quantity": 2,
        "discount_net": Decimal("0.00"),
        "tax_rate": Decimal("22"),
        "tax_reason": "domestic",
        "legal_note_key": "",
    }
    values.update(overrides)
    return LineInput(**values)  # type: ignore[arg-type]


def vendor_input(vendor: object = None, **overrides: object) -> VendorOrderInput:
    values: dict[str, object] = {
        "vendor": vendor or VendorFactory.create(),
        "lines": [line_input()],
        "shipping_net": Decimal("5.00"),
        "shipping_tax": Decimal("1.10"),
        "shipping_choice": {"carrier": "A", "zone": "IT", "rate": "5.00"},
        "commission_snapshot": {"rule": "default", "percentage": "10", "fee": "2.00"},
        "tax_profile_snapshot": {"regime": "standard"},
    }
    values.update(overrides)
    return VendorOrderInput(**values)  # type: ignore[arg-type]


def place(**overrides: object) -> Order:
    values: dict[str, object] = {
        "email": "buyer@example.com",
        "language": "it",
        "billing_address": {"line1": "1 Via Test", "city": "Roma"},
        "billing_country": "IT",
        "shipping_address": {"line1": "1 Via Test", "city": "Roma"},
        "shipping_country": "IT",
        "vendor_orders": [vendor_input()],
        "ctx": CTX,
        "now": NOW,
    }
    values.update(overrides)
    order, _ = services.create_order(**values)  # type: ignore[arg-type]
    assert order is not None
    return order


@pytest.mark.django_db
def test_numbers_are_sequential_within_the_year() -> None:
    first, second = place(), place()
    assert (first.number, second.number) == ("2027-000001", "2027-000002")
    assert place(now=dt.datetime(2028, 1, 2, tzinfo=dt.UTC)).number == "2028-000001"


@pytest.mark.django_db
def test_the_number_prefix_is_configurable(settings: object) -> None:
    settings.ORDER_NUMBER_PREFIX = "MP-"  # type: ignore[attr-defined]
    assert place().number == "MP-2027-000001"


@pytest.mark.django_db
def test_the_transfer_group_is_the_order_number() -> None:
    order = place()
    assert order.transfer_group == order.number


@pytest.mark.django_db
def test_a_rolled_back_order_leaves_no_gap() -> None:
    def failing_checkout() -> None:
        with transaction.atomic():
            place()
            raise RuntimeError("payment setup failed")

    with pytest.raises(RuntimeError, match="payment setup failed"):
        failing_checkout()
    assert place().number == "2027-000001"


@pytest.mark.django_db
def test_a_dry_run_changes_nothing_and_burns_no_number() -> None:
    order, changeset = services.create_order(
        email="buyer@example.com",
        language="en",
        billing_address={},
        billing_country="IT",
        shipping_address={},
        shipping_country="IT",
        vendor_orders=[vendor_input()],
        ctx=CTX,
        now=NOW,
        dry_run=True,
    )
    assert order is None
    assert not changeset.is_empty
    assert Order.objects.count() == 0
    assert OrderNumberSequence.objects.count() == 0
    assert AuditLog.objects.count() == 0
    assert place().number == "2027-000001"


@pytest.mark.django_db(transaction=True)
def test_two_concurrent_transactions_get_different_numbers() -> None:
    barrier = threading.Barrier(2)
    numbers: list[str] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            with transaction.atomic():
                barrier.wait(timeout=10)
                numbers.append(services.next_order_number(now=NOW))
        except BaseException as exc:
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not errors
    assert sorted(numbers) == ["2027-000001", "2027-000002"]


@pytest.mark.django_db
def test_totals_are_computed_and_stored_per_vendor_and_overall() -> None:
    order = place(
        vendor_orders=[
            vendor_input(),  # goods 20.00, line tax 4.40, shipping 5.00 + 1.10
            vendor_input(
                lines=[line_input(unit_price_net=Decimal("0.335"), quantity=3)],
                shipping_net=Decimal("0.00"),
                shipping_tax=Decimal("0.00"),
            ),
        ]
    )
    first, second = order.vendor_orders.order_by("id")
    assert (first.goods_net, first.shipping_net, first.tax_total, first.gross_total) == (
        Decimal("20.00"),
        Decimal("5.00"),
        Decimal("5.50"),
        Decimal("30.50"),
    )
    assert (second.goods_net, second.tax_total, second.gross_total) == (
        Decimal("1.01"),
        Decimal("0.22"),
        Decimal("1.23"),
    )
    assert order.goods_net == Decimal("21.01")
    assert order.shipping_net == Decimal("5.00")
    assert order.tax_total == Decimal("5.72")
    assert order.grand_total == Decimal("31.73")
    assert order.state == "checkout"


@pytest.mark.django_db
def test_lines_and_sub_orders_are_snapshots() -> None:
    product = ProductFactory.create(sku="VENDOR-SKU-1", price_net=Decimal("10.00"))
    order = place(
        vendor_orders=[
            vendor_input(
                vendor=product.vendor,
                lines=[line_input(product=product, legal_note_key="reverse_charge")],
            )
        ]
    )
    sub = order.vendor_orders.get()
    line = sub.lines.get()
    assert (line.sku, line.title, line.variant_label) == ("VENDOR-SKU-1", "Ceramic cup", "Blue")
    assert (line.unit_price_net, line.quantity, line.net_total) == (
        Decimal("10.00"),
        2,
        Decimal("20.00"),
    )
    assert (line.tax_rate, line.tax_reason, line.tax_amount) == (
        Decimal("22"),
        "domestic",
        Decimal("4.40"),
    )
    assert line.legal_note_key == "reverse_charge"
    assert sub.commission_snapshot == {"rule": "default", "percentage": "10", "fee": "2.00"}
    assert sub.shipping_choice["carrier"] == "A"
    assert sub.tax_profile_snapshot == {"regime": "standard"}
    # later catalogue edits never reach the order
    product.price_net = Decimal("99.00")
    product.save()
    line.refresh_from_db()
    assert line.unit_price_net == Decimal("10.00")


@pytest.mark.django_db
def test_creating_an_order_is_audited_without_credentials_of_the_buyer() -> None:
    order = place()
    entry = AuditLog.objects.get(action="orders.create", target_model="orders.order")
    assert entry.target_key == str(order.public_id)
    assert entry.after is not None
    assert entry.after["number"] == order.number
    assert AuditLog.objects.filter(target_model="orders.vendororder").count() == 1
    vendor_entry = AuditLog.objects.get(target_model="orders.vendororder")
    assert vendor_entry.vendor_key == str(order.vendor_orders.get().vendor.public_id)


@pytest.mark.django_db
def test_an_empty_order_or_a_foreign_state_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one vendor order"):
        place(vendor_orders=[])
    with pytest.raises(ValueError, match="at least one line"):
        place(vendor_orders=[vendor_input(lines=[])])
    with pytest.raises(ValueError, match="initial state"):
        place(state="paid")


@pytest.mark.django_db
def test_two_sub_orders_for_the_same_vendor_are_refused() -> None:
    vendor = VendorFactory.create()
    with pytest.raises(ValueError, match="same vendor"):
        place(vendor_orders=[vendor_input(vendor), vendor_input(vendor)])


@pytest.mark.django_db
def test_transition_order_is_audited_and_dry_run_able() -> None:
    order = place()
    changeset = services.transition_order(order, "awaiting_payment", ctx=CTX, dry_run=True)
    assert not changeset.is_empty
    order.refresh_from_db()
    assert order.state == "checkout"
    services.transition_order(order, "awaiting_payment", ctx=CTX)
    order.refresh_from_db()
    assert order.state == "awaiting_payment"
    entry = AuditLog.objects.get(action="orders.transition", target_model="orders.order")
    assert entry.before is not None
    assert entry.after is not None
    assert (entry.before["state"], entry.after["state"]) == ("checkout", "awaiting_payment")


@pytest.mark.django_db
def test_transition_order_refuses_illegal_moves_and_changes_nothing() -> None:
    order = place()
    with pytest.raises(IllegalTransition):
        services.transition_order(order, "paid", ctx=CTX)
    with pytest.raises(IllegalTransition):
        services.transition_order(order, "paid", ctx=CTX, dry_run=True)
    order.refresh_from_db()
    assert order.state == "checkout"
    assert not AuditLog.objects.filter(action="orders.transition").exists()


@pytest.mark.django_db
def test_vendor_order_shipping_and_delivery_stamp_dates_and_roll_up_the_order() -> None:
    order = place(vendor_orders=[vendor_input(), vendor_input()])
    services.transition_order(order, "awaiting_payment", ctx=CTX)
    services.transition_order(order, "paid", ctx=CTX)
    first, second = order.vendor_orders.order_by("id")

    services.transition_vendor_order(first, "accepted", ctx=CTX)
    order.refresh_from_db()
    assert order.state == "in_fulfillment"

    with pytest.raises(IllegalTransition):
        services.transition_vendor_order(first, "delivered", ctx=CTX)
    services.transition_vendor_order(
        first, "shipped", ctx=CTX, tracking_number="TRK123", carrier="Carrier A", now=NOW
    )
    first.refresh_from_db()
    assert (first.tracking_number, first.carrier, first.shipped_at) == ("TRK123", "Carrier A", NOW)
    order.refresh_from_db()
    assert order.state == "in_fulfillment"  # the second vendor has not shipped

    services.transition_vendor_order(second, "accepted", ctx=CTX)
    services.transition_vendor_order(second, "shipped", ctx=CTX, tracking_number="T2", now=NOW)
    order.refresh_from_db()
    assert order.state == "fulfilled"
    services.transition_vendor_order(first, "delivered", ctx=CTX, now=NOW)
    first.refresh_from_db()
    assert first.delivered_at == NOW


@pytest.mark.django_db
def test_shipping_needs_a_tracking_number() -> None:
    order = place()
    sub = order.vendor_orders.get()
    services.transition_vendor_order(sub, "accepted", ctx=CTX)
    with pytest.raises(ValueError, match="tracking number"):
        services.transition_vendor_order(sub, "shipped", ctx=CTX)
    assert VendorOrder.objects.get(pk=sub.pk).state == "accepted"


@pytest.mark.django_db
def test_vendor_order_transition_dry_run_changes_nothing() -> None:
    order = place()
    sub = order.vendor_orders.get()
    changeset = services.transition_vendor_order(sub, "accepted", ctx=CTX, dry_run=True)
    assert not changeset.is_empty
    assert VendorOrder.objects.get(pk=sub.pk).state == "pending"
    assert not AuditLog.objects.filter(action="orders.transition").exists()
