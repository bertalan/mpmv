"""ORD.2: order creation with snapshots, numbering and audited, dry-run-able transitions."""

import datetime as dt
import threading
import time
from decimal import Decimal
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, transaction

from apps.catalog.tests.factories import ProductFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.orders import services
from apps.orders.engine import IllegalTransition
from apps.orders.models import Order, OrderNumberSequence, VendorOrder
from apps.orders.services import LineInput, VendorOrderInput
from apps.orders.tests.factories import OrderNumberSequenceFactory
from apps.vendors.tests.factories import VendorFactory

COMMISSION = {
    "rule_id": "default",
    "version": 3,
    "percent": "10.00",
    "base": "goods",
    "fee_percent": "1.40",
    "fee_fixed": "0.25",
    "fee_base": "gross",
    "promo_label": "",
    "valid_from": "2027-01-01T00:00:00+00:00",
}
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
        "shipping_tax_rate": Decimal("22"),
        "shipping_tax_reason": "domestic",
        "shipping_choice": {"carrier": "A", "zone": "IT", "rate": "5.00"},
        "commission_snapshot": COMMISSION,
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


def pay(order: Order) -> None:
    services.transition_order(order, "awaiting_payment", ctx=CTX)
    services.transition_order(order, "paid", ctx=CTX)


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
                numbers.append(services._take_order_number(now=NOW))
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
                lines=[line_input(unit_price_net=Decimal("0.33"), quantity=3)],
                shipping_net=Decimal("0.00"),
                shipping_tax_rate=Decimal("0"),
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
        Decimal("0.99"),
        Decimal("0.22"),
        Decimal("1.21"),
    )
    assert order.goods_net == Decimal("20.99")
    assert order.shipping_net == Decimal("5.00")
    assert order.tax_total == Decimal("5.72")
    assert order.grand_total == Decimal("31.71")
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
    assert sub.commission_snapshot == COMMISSION
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
    pay(order)
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
    pay(order)
    sub = order.vendor_orders.get()
    services.transition_vendor_order(sub, "accepted", ctx=CTX)
    with pytest.raises(ValueError, match="tracking number"):
        services.transition_vendor_order(sub, "shipped", ctx=CTX)
    assert VendorOrder.objects.get(pk=sub.pk).state == "accepted"


@pytest.mark.django_db
def test_vendor_order_transition_dry_run_changes_nothing() -> None:
    order = place()
    pay(order)
    audited = AuditLog.objects.filter(action="orders.transition").count()
    sub = order.vendor_orders.get()
    changeset = services.transition_vendor_order(sub, "accepted", ctx=CTX, dry_run=True)
    assert not changeset.is_empty
    assert VendorOrder.objects.get(pk=sub.pk).state == "pending"
    assert Order.objects.get(pk=order.pk).state == "paid"
    assert AuditLog.objects.filter(action="orders.transition").count() == audited


@pytest.mark.django_db
def test_a_number_preview_does_not_consume_the_number() -> None:
    assert services.peek_order_number(now=NOW) == "2027-000001"
    assert services.peek_order_number(now=NOW) == "2027-000001"
    assert OrderNumberSequence.objects.count() == 0
    assert place().number == "2027-000001"
    assert services.peek_order_number(now=NOW) == "2027-000002"


@pytest.mark.django_db
def test_numbering_continues_from_the_stored_counter() -> None:
    OrderNumberSequenceFactory.create(year=2027, last_value=41)
    assert place().number == "2027-000042"


@pytest.mark.django_db
def test_the_numbering_year_is_the_business_year_in_rome() -> None:
    # 23:30 UTC on 31 December is 00:30 on 1 January in Rome.
    assert place(now=dt.datetime(2027, 12, 31, 23, 30, tzinfo=dt.UTC)).number == "2028-000001"
    assert place(now=dt.datetime(2027, 12, 31, 22, 30, tzinfo=dt.UTC)).number == "2027-000001"
    # 22:30 UTC on 30 June is 00:30 on 1 July in Rome: same year, no rollover.
    assert place(now=dt.datetime(2027, 6, 30, 22, 30, tzinfo=dt.UTC)).number == "2027-000002"


@pytest.mark.django_db
def test_an_overlong_prefix_is_refused(settings: object) -> None:
    settings.ORDER_NUMBER_PREFIX = "X" * 15  # type: ignore[attr-defined]
    with pytest.raises(ImproperlyConfigured, match="ORDER_NUMBER_PREFIX"):
        place()


@pytest.mark.django_db
def test_shipping_tax_is_snapshotted_on_the_sub_order() -> None:
    order = place(
        vendor_orders=[
            vendor_input(
                shipping_tax_reason="domestic",
                shipping_vat_nature_code="N2.2",
                lines=[line_input(vat_nature_code="N3.5")],
            )
        ]
    )
    sub = order.vendor_orders.get()
    assert (sub.shipping_tax_rate, sub.shipping_tax_amount) == (Decimal("22"), Decimal("1.10"))
    assert (sub.shipping_tax_reason, sub.shipping_vat_nature_code) == ("domestic", "N2.2")
    assert sub.lines.get().vat_nature_code == "N3.5"
    assert sub.tax_total == Decimal("4.40") + Decimal("1.10")


@pytest.mark.django_db
def test_the_vendor_identity_is_snapshotted() -> None:
    vendor = VendorFactory.create(display_name="Cups Ltd", legal_name="Cups S.r.l.")
    sub = place(vendor_orders=[vendor_input(vendor)]).vendor_orders.get()
    assert sub.vendor_snapshot["display_name"] == "Cups Ltd"
    assert sub.vendor_snapshot["legal_name"] == "Cups S.r.l."
    assert set(sub.vendor_snapshot) >= {"vat_number", "country"}
    vendor.display_name = "Renamed"
    vendor.save()
    sub.refresh_from_db()
    assert sub.vendor_snapshot["display_name"] == "Cups Ltd"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "missing", ["rule_id", "version", "percent", "base", "fee_percent", "fee_fixed", "fee_base"]
)
def test_the_commission_snapshot_needs_its_keys(missing: str) -> None:
    snapshot = {k: v for k, v in COMMISSION.items() if k != missing}
    with pytest.raises(ValueError, match="commission_snapshot"):
        place(vendor_orders=[vendor_input(commission_snapshot=snapshot)])


@pytest.mark.django_db
def test_negative_shipping_and_zero_prices_are_refused() -> None:
    with pytest.raises(ValueError, match="shipping"):
        place(vendor_orders=[vendor_input(shipping_net=Decimal("-1.00"))])
    with pytest.raises(ValueError, match="price"):
        place(vendor_orders=[vendor_input(lines=[line_input(unit_price_net=Decimal("0.00"))])])
    with pytest.raises(ValueError, match="cents"):
        place(vendor_orders=[vendor_input(lines=[line_input(unit_price_net=Decimal("0.335"))])])


@pytest.mark.django_db
def test_a_vendor_order_of_an_unpaid_order_cannot_move() -> None:
    order = place()
    sub = order.vendor_orders.get()
    with pytest.raises(ValueError, match="paid"):
        services.transition_vendor_order(sub, "accepted", ctx=CTX)
    services.transition_order(order, "awaiting_payment", ctx=CTX)
    with pytest.raises(ValueError, match="paid"):
        services.transition_vendor_order(sub, "accepted", ctx=CTX, dry_run=True)


@pytest.mark.django_db(transaction=True)
def test_a_rolled_back_number_is_reused_by_the_waiting_order_through_create_order() -> None:
    vendors = [VendorFactory.create(), VendorFactory.create()]
    a_has_number = threading.Event()
    results: dict[str, Any] = {}
    errors: list[BaseException] = []

    def rolled_back() -> None:
        try:
            try:
                with transaction.atomic():
                    order, _ = services.create_order(**_kwargs(vendors[0]))  # type: ignore[arg-type]
                    assert order is not None
                    results["a"] = order.number
                    a_has_number.set()
                    time.sleep(1.0)  # B is now blocked on the counter row
                    raise RuntimeError("payment setup failed")
            except RuntimeError:
                pass
        except BaseException as exc:
            errors.append(exc)
        finally:
            a_has_number.set()
            connection.close()

    def waiting() -> None:
        try:
            a_has_number.wait(timeout=10)
            started = time.monotonic()
            order, _ = services.create_order(**_kwargs(vendors[1]))  # type: ignore[arg-type]
            assert order is not None
            results["b"] = order.number
            results["b_waited"] = time.monotonic() - started
        except BaseException as exc:
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=rolled_back), threading.Thread(target=waiting)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not errors
    assert results["a"] == results["b"] == "2027-000001"
    assert results["b_waited"] > 0.3  # it queued behind A's lock


def _kwargs(vendor: object) -> dict[str, object]:
    return {
        "email": "buyer@example.com",
        "language": "en",
        "billing_address": {},
        "billing_country": "IT",
        "shipping_address": {},
        "shipping_country": "IT",
        "vendor_orders": [vendor_input(vendor)],
        "ctx": CTX,
        "now": NOW,
    }


@pytest.mark.django_db
def test_the_commission_snapshot_refuses_unknown_keys_and_allows_the_optional_ones() -> None:
    with pytest.raises(ValueError, match="unknown keys"):
        place(vendor_orders=[vendor_input(commission_snapshot={**COMMISSION, "fee": "2.00"})])
    minimal = {k: v for k, v in COMMISSION.items() if k not in ("promo_label", "valid_from")}
    place(vendor_orders=[vendor_input(commission_snapshot=minimal)])
