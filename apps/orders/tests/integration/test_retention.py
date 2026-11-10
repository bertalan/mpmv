"""ORD.2, owner decision D-29: fiscal snapshot fields are immutable; only the audited scrub
service (minimise after the active phase, anonymise after year 10) may change them."""

import datetime as dt
from decimal import Decimal

import pytest
from django.db import IntegrityError, connection, transaction

from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.orders import engine, services
from apps.orders.models import Order, VendorOrder
from apps.orders.services import LineInput, ScrubNotDue, VendorOrderInput
from apps.orders.tests.factories import OrderFactory, VendorOrderFactory
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="system:test", source=AuditLog.Source.SYSTEM)
PLACED = dt.datetime(2027, 3, 15, 10, 0, tzinfo=dt.UTC)
COMMISSION = {"rule_id": "default", "rule_version": 1, "percentage": "10", "fee": "1.00"}


def run_sql(*statements: tuple[str, list[object]]) -> None:
    with transaction.atomic(), connection.cursor() as cursor:
        for sql, params in statements:
            cursor.execute(sql, params)


def place(**overrides: object) -> Order:
    sub = VendorOrderInput(
        vendor=VendorFactory.create(),
        lines=[
            LineInput(
                product=None,
                sku="S1",
                title="Cup",
                unit_price_net=Decimal("10.00"),
                quantity=1,
                tax_rate=Decimal("22"),
                tax_reason="domestic",
            )
        ],
        commission_snapshot=COMMISSION,
    )
    values: dict[str, object] = {
        "email": "buyer@example.com",
        "language": "it",
        "billing_address": {"line1": "1 Via Test", "city": "Roma"},
        "billing_country": "IT",
        "shipping_address": {"line1": "2 Via Test", "city": "Milano"},
        "shipping_country": "IT",
        "customer_name": "Mario Rossi",
        "customer_vat_number": "IT12345678901",
        "customer_tax_id": "RSSMRA80A01H501U",
        "vat_validation": {"consultation": "WAPIAAAA", "at": "2027-03-15T10:00:00Z"},
        "vendor_orders": [sub],
        "ctx": CTX,
        "now": PLACED,
    }
    values.update(overrides)
    order, _ = services.create_order(**values)  # type: ignore[arg-type]
    assert order is not None
    return order


# --- MIG.4: legacy orders may be inserted in any state ----------------------------------------


def test_a_legacy_order_may_be_created_in_a_final_state() -> None:
    order = place(state="closed", legacy_wp_order_id=4711)
    assert Order.objects.get(pk=order.pk).state == "closed"


def test_a_legacy_sub_order_may_be_created_in_a_final_state() -> None:
    sub = VendorOrderInput(
        vendor=VendorFactory.create(),
        lines=[
            LineInput(
                product=None,
                sku="S1",
                title="Cup",
                unit_price_net=Decimal("10.00"),
                quantity=1,
                tax_rate=Decimal("22"),
                tax_reason="domestic",
            )
        ],
        commission_snapshot=COMMISSION,
        state="completed",
    )
    order = place(state="closed", legacy_wp_order_id=4712, vendor_orders=[sub])
    assert order.vendor_orders.get().state == "completed"


def test_a_non_legacy_order_keeps_the_initial_state_rule() -> None:
    with pytest.raises(ValueError, match="initial state"):
        place(state="closed")
    sub = VendorOrderInput(
        vendor=VendorFactory.create(),
        lines=[
            LineInput(
                product=None,
                sku="S1",
                title="Cup",
                unit_price_net=Decimal("10.00"),
                quantity=1,
                tax_rate=Decimal("22"),
                tax_reason="domestic",
            )
        ],
        commission_snapshot=COMMISSION,
        state="completed",
    )
    with pytest.raises(ValueError, match="legacy"):
        place(vendor_orders=[sub])


def test_the_database_accepts_a_final_state_only_with_a_legacy_id() -> None:
    kwargs = {"number": "2998-000101", "email": "a@example.com", "billing_country": "IT",
              "shipping_country": "IT", "goods_net": 0, "shipping_net": 0, "tax_total": 0,
              "grand_total": 0}  # fmt: skip
    with pytest.raises(IntegrityError), transaction.atomic():
        Order.objects.bulk_create([Order(**kwargs, state="closed")])
    Order.objects.bulk_create([Order(**kwargs, state="closed", legacy_wp_order_id=99)])


def test_the_save_guard_follows_the_same_legacy_rule() -> None:
    order = OrderFactory.build(state="closed", legacy_wp_order_id=5)
    order.save()
    assert Order.objects.get(pk=order.pk).state == "closed"


# --- D-29: the fiscal fields are immutable ----------------------------------------------------


FISCAL = {
    "email": "other@example.com",
    "customer_name": "Someone Else",
    "customer_vat_number": "IT00000000000",
    "customer_tax_id": "XXXXXXXXXXXXXXXX",
    "billing_address": {"line1": "other"},
    "shipping_address": {"line1": "other"},
    "billing_country": "DE",
    "shipping_country": "DE",
    "vat_validation": {"consultation": "FAKE"},
}


@pytest.mark.parametrize(("column", "value"), list(FISCAL.items()))
def test_a_direct_update_of_a_fiscal_field_fails(column: str, value: object) -> None:
    order = place()
    with pytest.raises(IntegrityError), transaction.atomic():
        Order.objects.filter(pk=order.pk).update(**{column: value})


def test_raw_sql_cannot_change_an_address_either() -> None:
    order = place()
    with pytest.raises(IntegrityError):
        run_sql(("UPDATE orders_order SET billing_address = '{}' WHERE id = %s", [order.pk]))


@pytest.mark.parametrize(
    ("mode", "assignment"),
    [
        ("minimise", "email = 'x@example.com'"),
        ("anonymise", "grand_total = 1"),
        ("anonymise", "shipping_country = 'DE'"),
    ],
)
def test_a_client_setting_the_scrub_mode_does_not_unlock_other_columns(
    mode: str, assignment: str
) -> None:
    order = place()
    with pytest.raises(IntegrityError):
        run_sql(
            ("SELECT set_config('mpmv.order_scrub', %s, true)", [mode]),
            (f"UPDATE orders_order SET {assignment} WHERE id = %s", [order.pk]),  # noqa: S608
        )


def test_orders_and_sub_orders_cannot_be_deleted() -> None:
    sub = VendorOrderFactory.create()
    with pytest.raises(IntegrityError), transaction.atomic():
        VendorOrder.objects.filter(pk=sub.pk).delete()
    with pytest.raises(IntegrityError), transaction.atomic():
        Order.objects.filter(pk=sub.order_id).delete()


# --- the scrub service ------------------------------------------------------------------------


def with_phone(order: Order) -> VendorOrder:
    sub = order.vendor_orders.get()
    VendorOrder.objects.filter(pk=sub.pk).update(
        carrier_contact_phone="+39 0000 000", support_notes="called twice"
    )
    return VendorOrder.objects.get(pk=sub.pk)


def test_minimise_before_the_active_phase_is_refused() -> None:
    order = place()
    with pytest.raises(ScrubNotDue):
        services.minimise_order(order, ctx=CTX, now=PLACED + dt.timedelta(days=700))
    with pytest.raises(ScrubNotDue):
        services.minimise_order(order, ctx=CTX, now=PLACED + dt.timedelta(days=700), dry_run=True)


def test_minimise_drops_only_the_non_fiscal_fields_and_audits() -> None:
    order = place()
    with_phone(order)
    later = PLACED + dt.timedelta(days=800)
    changeset = services.minimise_order(order, ctx=CTX, now=later)
    assert not changeset.is_empty
    sub = order.vendor_orders.get()
    assert (sub.carrier_contact_phone, sub.support_notes) == ("", "")
    order.refresh_from_db()
    assert order.minimised_at == later
    assert order.email == "buyer@example.com"  # fiscal data stays
    assert order.billing_address["city"] == "Roma"
    entry = AuditLog.objects.get(action="orders.minimise")
    assert "+39" not in str(entry.before) + str(entry.after)


def test_the_active_phase_is_configurable(settings: object) -> None:
    settings.ORDER_ACTIVE_PHASE_MONTHS = 6  # type: ignore[attr-defined]
    order = place()
    services.minimise_order(order, ctx=CTX, now=PLACED + dt.timedelta(days=200))
    order.refresh_from_db()
    assert order.minimised_at is not None


def test_a_dry_run_scrub_writes_nothing() -> None:
    order = place()
    with_phone(order)
    audited = AuditLog.objects.count()
    changeset = services.minimise_order(
        order, ctx=CTX, now=PLACED + dt.timedelta(days=800), dry_run=True
    )
    assert not changeset.is_empty
    assert order.vendor_orders.get().carrier_contact_phone == "+39 0000 000"
    order.refresh_from_db()
    assert order.minimised_at is None
    assert AuditLog.objects.count() == audited

    far = dt.datetime(2038, 2, 1, tzinfo=dt.UTC)
    changeset = services.anonymise_order(order, ctx=CTX, now=far, dry_run=True)
    assert not changeset.is_empty
    order.refresh_from_db()
    assert order.email == "buyer@example.com"
    assert AuditLog.objects.count() == audited


def test_anonymise_before_year_ten_is_refused() -> None:
    order = place()
    with pytest.raises(ScrubNotDue):
        services.anonymise_order(order, ctx=CTX, now=dt.datetime(2037, 12, 31, 12, tzinfo=dt.UTC))
    order.refresh_from_db()
    assert order.anonymised_at is None


def test_anonymise_replaces_personal_fields_and_keeps_money_country_and_rate() -> None:
    order = place()
    with_phone(order)
    before_money = (order.goods_net, order.tax_total, order.grand_total, order.shipping_country)
    now = dt.datetime(2038, 1, 2, tzinfo=dt.UTC)
    services.anonymise_order(order, ctx=CTX, now=now)
    order.refresh_from_db()
    assert order.anonymised_at == now
    assert order.email == engine.ANONYMISED_EMAIL
    assert order.customer_name == engine.ANONYMISED
    assert "Mario" not in str((order.billing_address, order.shipping_address, order.vat_validation))
    assert order.customer is None
    assert (order.goods_net, order.tax_total, order.grand_total, order.shipping_country) == (
        before_money
    )
    sub = order.vendor_orders.get()
    assert sub.carrier_contact_phone == ""
    assert sub.lines.get().tax_rate == Decimal("22.000")
    entry = AuditLog.objects.get(action="orders.anonymise")
    assert "Rossi" not in str(entry.before) + str(entry.after)
    # idempotent
    assert services.anonymise_order(order, ctx=CTX, now=now).is_empty


def test_after_anonymisation_the_personal_fields_are_immutable_again() -> None:
    order = place()
    services.anonymise_order(order, ctx=CTX, now=dt.datetime(2038, 1, 2, tzinfo=dt.UTC))
    with pytest.raises(IntegrityError), transaction.atomic():
        Order.objects.filter(pk=order.pk).update(email="back@example.com")


def test_the_year_is_the_business_year() -> None:
    order = place(now=dt.datetime(2027, 12, 31, 23, 30, tzinfo=dt.UTC))  # 2028 in Rome
    with pytest.raises(ScrubNotDue):
        services.anonymise_order(order, ctx=CTX, now=dt.datetime(2038, 6, 1, tzinfo=dt.UTC))
    services.anonymise_order(order, ctx=CTX, now=dt.datetime(2039, 1, 1, tzinfo=dt.UTC))
