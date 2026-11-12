"""VEN.4: the append-only ledger on real PostgreSQL: refusals, idempotency, audit, balances."""

import datetime as dt
from decimal import Decimal

import pytest
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from apps.core.audit import AuditContext
from apps.core.models import AuditLog, PlatformOperator
from apps.core.tests.factories import PlatformOperatorFactory
from apps.ledger import engine, services
from apps.ledger.models import LedgerEntry
from apps.ledger.tests.factories import LedgerEntryFactory
from apps.orders.tests.factories import VendorOrderFactory
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="system:test", source="system")


@pytest.fixture(autouse=True)
def operator() -> None:
    PlatformOperatorFactory.create(
        valid_from=dt.datetime(2020, 1, 1, tzinfo=dt.UTC), open_ended=True
    )


def record(vendor, kind: str, amount: str, key: str, **extra):  # type: ignore[no-untyped-def]
    return services.record_entry(
        vendor, kind=kind, amount=Decimal(amount), idempotency_key=key, ctx=CTX, **extra
    )


# --- Append-only


def test_update_and_delete_are_refused_by_the_database() -> None:
    entry = LedgerEntryFactory.create()
    rows = LedgerEntry.objects.filter(pk=entry.pk)
    with pytest.raises(DatabaseError, match="append-only"), transaction.atomic():
        rows.update(amount=Decimal("99.00"))
    with pytest.raises(DatabaseError, match="append-only"), transaction.atomic():
        rows.delete()
    with (
        pytest.raises(DatabaseError, match="append-only"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("UPDATE ledger_ledgerentry SET memo = 'x' WHERE id = %s", [entry.pk])
    with (
        pytest.raises(DatabaseError, match="append-only"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("DELETE FROM ledger_ledgerentry WHERE id = %s", [entry.pk])
    assert rows.get().amount == Decimal("10.00")


def test_the_model_refuses_a_second_save_and_a_delete() -> None:
    entry = LedgerEntryFactory.create()
    with pytest.raises(PermissionError):
        entry.save()
    with pytest.raises(PermissionError):
        entry.delete()


def test_the_database_refuses_a_zero_amount() -> None:
    entry = LedgerEntryFactory.build(vendor=VendorFactory.create(), amount=Decimal("0.00"))
    entry.platform_entity = PlatformOperator.objects.get()
    with pytest.raises(DatabaseError), transaction.atomic():
        entry.save()


# --- record_entry


def test_an_entry_is_recorded_audited_and_attributed_to_the_current_operator() -> None:
    vendor = VendorFactory.create()
    changes = record(vendor, "sale", "100.00", "k1", source_ref="order-snapshot-1")
    entry = LedgerEntry.objects.get()
    assert changes.summary()["create"] == 1
    assert entry.amount == Decimal("100.00")
    assert entry.balance_after == Decimal("100.00")
    assert entry.currency == "EUR"
    assert entry.created_by == "system:test"
    assert entry.platform_entity.valid_to is None
    log = AuditLog.objects.get(action="ledger.entry_recorded")
    assert log.vendor_key == str(vendor.public_id)
    assert log.after is not None
    assert log.after["amount"] == "100.00"


def test_dry_run_returns_the_changeset_and_writes_nothing() -> None:
    vendor = VendorFactory.create()
    changes = record(vendor, "sale", "5.00", "k1", dry_run=True)
    assert changes.summary()["create"] == 1
    assert not LedgerEntry.objects.exists()
    assert not AuditLog.objects.filter(action="ledger.entry_recorded").exists()


def test_the_same_key_and_content_is_a_no_op() -> None:
    vendor = VendorFactory.create()
    record(vendor, "sale", "5.00", "k1")
    again = record(vendor, "sale", "5.00", "k1")
    assert again.is_empty
    assert LedgerEntry.objects.count() == 1
    assert AuditLog.objects.filter(action="ledger.entry_recorded").count() == 1


def test_the_same_key_with_other_content_is_refused() -> None:
    vendor = VendorFactory.create()
    record(vendor, "sale", "5.00", "k1")
    with pytest.raises(engine.IdempotencyConflict):
        record(vendor, "sale", "6.00", "k1")
    with pytest.raises(engine.IdempotencyConflict):
        record(VendorFactory.create(), "sale", "5.00", "k1")


def test_the_key_is_unique_in_the_database() -> None:
    entry = LedgerEntryFactory.create()
    duplicate = LedgerEntryFactory.build(vendor=entry.vendor, idempotency_key=entry.idempotency_key)
    duplicate.platform_entity = entry.platform_entity
    with pytest.raises(DatabaseError), transaction.atomic():
        duplicate.save()


def test_wrong_sign_unknown_kind_and_foreign_vendor_order_are_refused() -> None:
    vendor = VendorFactory.create()
    with pytest.raises(engine.InvalidEntry):
        record(vendor, "sale", "-5.00", "k1")
    with pytest.raises(engine.InvalidEntry):
        record(vendor, "commission", "5.00", "k2")
    with pytest.raises(engine.InvalidEntry):
        record(vendor, "gift", "5.00", "k3")
    with pytest.raises(engine.InvalidEntry):
        record(vendor, "sale", "5.00", " ")
    other_order = VendorOrderFactory.create()
    with pytest.raises(engine.InvalidEntry, match="another vendor"):
        record(vendor, "sale", "5.00", "k4", vendor_order=other_order)
    assert not LedgerEntry.objects.exists()


def test_an_entry_may_reference_the_vendors_own_order() -> None:
    order = VendorOrderFactory.create()
    record(order.vendor, "sale", "12.20", "k1", vendor_order=order)
    assert LedgerEntry.objects.get().vendor_order == order


def test_amounts_are_rounded_half_up() -> None:
    vendor = VendorFactory.create()
    record(vendor, "sale", "10.005", "k1")
    assert LedgerEntry.objects.get().amount == Decimal("10.01")


# --- Balances


def test_balance_is_the_sum_of_a_sale_its_fees_and_a_refund() -> None:
    vendor = VendorFactory.create()
    record(vendor, "sale", "100.00", "sale")
    record(vendor, "shipping", "5.00", "ship")
    record(vendor, "commission", "-10.00", "comm")
    record(vendor, "payment_fee", "-2.50", "fee")
    assert services.vendor_balance(vendor) == Decimal("92.50")
    record(vendor, "refund", "-52.50", "refund")  # half of the goods and the shipping
    record(vendor, "commission_reversal", "5.00", "rev")
    record(vendor, "refund_fee", "-3.00", "rfee")
    assert services.vendor_balance(vendor) == Decimal("42.00")


def test_balance_may_go_negative_and_is_never_refused() -> None:
    vendor = VendorFactory.create()
    record(vendor, "sale", "10.00", "sale")
    record(vendor, "refund", "-10.00", "refund")
    record(vendor, "refund_fee", "-3.00", "rfee")
    assert services.vendor_balance(vendor) == Decimal("-3.00")
    record(vendor, "adjustment", "3.00", "fix")
    assert services.vendor_balance(vendor) == Decimal("0.00")


def test_balances_are_per_vendor() -> None:
    a, b = VendorFactory.create_batch(2)
    record(a, "sale", "10.00", "a1")
    record(b, "sale", "7.00", "b1")
    record(a, "commission", "-1.00", "a2")
    assert services.vendor_balance(a) == Decimal("9.00")
    assert services.vendor_balance(b) == Decimal("7.00")
    assert services.vendor_balance(VendorFactory.create()) == Decimal("0.00")


def test_running_balance_column_follows_each_vendors_entries() -> None:
    a, b = VendorFactory.create_batch(2)
    record(a, "sale", "10.00", "a1")
    record(b, "sale", "7.00", "b1")
    record(a, "commission", "-1.00", "a2")
    record(a, "payout", "-5.00", "a3")
    rows = LedgerEntry.objects.filter(vendor=a).order_by("id")
    assert [r.balance_after for r in rows] == [Decimal("10.00"), Decimal("9.00"), Decimal("4.00")]
    assert LedgerEntry.objects.get(vendor=b).balance_after == Decimal("7.00")
    last = rows.last()
    assert last is not None
    assert last.balance_after == services.vendor_balance(a)


def test_balance_until_a_moment_ignores_later_entries() -> None:
    vendor = VendorFactory.create()
    record(vendor, "sale", "10.00", "k1")
    cutoff = timezone.now()
    record(vendor, "sale", "5.00", "k2")
    assert services.vendor_balance(vendor, until=cutoff) == Decimal("10.00")
    assert services.vendor_balance(vendor) == Decimal("15.00")


def test_statement_has_opening_totals_by_kind_and_closing() -> None:
    vendor = VendorFactory.create()
    record(vendor, "sale", "20.00", "old")
    start = timezone.now()
    record(vendor, "sale", "100.00", "s1")
    record(vendor, "commission", "-10.00", "c1")
    record(vendor, "refund", "-30.00", "r1")
    end = timezone.now() + dt.timedelta(seconds=1)
    statement = services.vendor_statement(vendor, start, end)
    assert statement.opening == Decimal("20.00")
    assert statement.credits == Decimal("100.00")
    assert statement.debits == Decimal("-40.00")
    assert statement.closing == Decimal("80.00")
    assert statement.closing == services.vendor_balance(vendor)
    assert statement.by_kind["refund"] == Decimal("-30.00")
