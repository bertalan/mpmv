"""MIG.4 (part 1) done-when: the restricted tax-only archive of migrated legacy orders.

Visibility per role, number mapping with the legacy reference, refunds inside the order, the
canary check on stored data, commissions as recorded, and the 10-year retention job.
"""

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest
from django.db import IntegrityError, connection

from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.orders import archive, legacy
from apps.orders.models import Order, OrderLine, VendorOrder
from apps.orders.tests.legacy_records import CANARIES, COMMISSION, record
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db
CTX = AuditContext(actor_label="system:test", source=AuditLog.Source.SYSTEM)
NOW = dt.datetime(2026, 11, 12, 10, 0, tzinfo=dt.UTC)


@pytest.fixture
def vendors() -> dict[str, Any]:
    return {"A": VendorFactory.create()}


def load(rec: dict[str, Any], vendors: dict[str, Any], **kwargs: Any) -> Order:
    order, _ = archive.load_archive_order(rec, vendors=vendors, ctx=CTX, now=NOW, **kwargs)
    assert order is not None
    return order


# --- what is stored ----------------------------------------------------------------------------


def test_the_archive_keeps_only_the_tax_fields(vendors: dict[str, Any]) -> None:
    order = load(record(), vendors)
    assert order.tax_archive
    assert order.privacy_status == "restricted_tax_only"
    assert order.restricted_at is not None
    assert (order.customer_name, order.customer_vat_number) == ("Mario Rossi", "IT12345678901")
    assert order.billing_address["line1"] == "1 Via Roma"
    assert order.shipping_address == {}
    assert order.email == ""
    assert order.customer is None
    assert (order.goods_net, order.shipping_net) == (Decimal("20.00"), Decimal("5.00"))
    assert (order.tax_total, order.grand_total) == (Decimal("5.50"), Decimal("30.50"))
    assert order.state == "closed"
    assert order.created_at == dt.datetime(2024, 3, 10, 9, 30, tzinfo=dt.UTC)
    assert order.paid_at == dt.datetime(2024, 3, 10, 9, 35, tzinfo=dt.UTC)
    assert order.tax_retention_until == dt.date(2034, 12, 31)
    line = OrderLine.objects.get()
    assert (line.title, line.tax_amount) == ("Ceramic cup", Decimal("4.40"))
    assert line.tax_rate == Decimal("22")


def test_no_canary_value_is_stored_anywhere(vendors: dict[str, Any]) -> None:
    load(record(), vendors)
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_jsonb(o)::text FROM orders_order o")
        dump = cursor.fetchone()[0]
        cursor.execute("SELECT to_jsonb(v)::text FROM orders_vendororder v")
        dump += cursor.fetchone()[0]
        cursor.execute("SELECT to_jsonb(l)::text FROM orders_orderline l")
        dump += cursor.fetchone()[0]
    dump += repr(list(AuditLog.objects.values_list("before", "after")))
    for value in CANARIES.values():
        assert value not in dump


def test_the_commission_is_loaded_as_recorded_never_recomputed(vendors: dict[str, Any]) -> None:
    rec = record()
    rec["vendors"][0]["commission"]["percent"] = "17.00"  # unlike any current rule
    load(rec, vendors)
    assert VendorOrder.objects.get().commission_snapshot["percent"] == "17.00"
    assert COMMISSION["percent"] == "12.00"


def test_an_exempt_order_without_tax_lines_loads(vendors: dict[str, Any]) -> None:
    rec = record(tax_total="0.00")
    rec["vendors"][0]["lines"][0]["tax_rate"] = "0"
    rec["vendors"][0]["shipping_tax_rate"] = "0"
    assert load(rec, vendors).tax_total == Decimal("0.00")


def test_a_business_buyer_is_b2b_and_a_consumer_b2c(vendors: dict[str, Any]) -> None:
    assert load(record(1), vendors).customer_tax_status == "b2b"
    assert load(record(2, billing={"vat_id": ""}), vendors).customer_tax_status == "b2c"


# --- numbers -----------------------------------------------------------------------------------


def test_every_migrated_order_gets_a_new_number_and_keeps_the_legacy_reference(
    vendors: dict[str, Any],
) -> None:
    first, second = load(record(4711), vendors), load(record(4712), vendors)
    assert first.number == "2026-000001"
    assert second.number == "2026-000002"
    assert (first.legacy_wp_order_id, second.legacy_wp_order_id) == (4711, 4712)
    assert str(first.legacy_wp_order_id) not in first.number


def test_the_legacy_number_is_searchable_by_the_tax_audit_role(vendors: dict[str, Any]) -> None:
    order = load(record(4711), vendors)
    assert archive.find_archive_order(["tax_audit"], 4711) == order
    assert archive.find_archive_order(["tax_audit"], "4711") == order
    assert archive.find_archive_order(["tax_audit"], 9999) is None
    assert archive.find_archive_order(["tax_audit"], "x") is None
    assert archive.find_archive_order(["finance", "owner"], 4711) is None


def test_loading_the_same_legacy_order_twice_is_idempotent(vendors: dict[str, Any]) -> None:
    first = load(record(4711), vendors)
    again, changeset = archive.load_archive_order(record(4711), vendors=vendors, ctx=CTX, now=NOW)
    assert again == first
    assert changeset.is_empty
    assert Order.objects.count() == 1


# --- refunds (MIG.20) ----------------------------------------------------------------------------


def test_refunds_are_stored_inside_the_order_with_no_ledger_or_tax_change(
    vendors: dict[str, Any],
) -> None:
    rec = record(refunds=[{"amount": "5.00", "date": "2024-04-01", "reason": "damaged"}])
    order = load(rec, vendors)
    assert order.historical_refunds == [
        {"amount": "5.00", "date": "2024-04-01", "reason": "damaged"}
    ]
    assert archive.refunded_total(order) == Decimal("5.00")
    assert order.state == "partially_refunded"
    assert (order.tax_total, order.grand_total) == (Decimal("5.50"), Decimal("30.50"))
    assert VendorOrder.objects.get().state == "completed"


def test_a_malformed_refund_loads_nothing(vendors: dict[str, Any]) -> None:
    rec = record(refunds=[{"amount": "5,00", "date": "2024-04-01", "reason": ""}])
    with pytest.raises(legacy.LegacyRecordError):
        archive.load_archive_order(rec, vendors=vendors, ctx=CTX, now=NOW)
    assert not Order.objects.exists()


# --- refusals ------------------------------------------------------------------------------------


def test_an_order_in_progress_is_not_loaded(vendors: dict[str, Any]) -> None:
    with pytest.raises(legacy.OrderInProgress):
        archive.load_archive_order(record(status="processing"), vendors=vendors, ctx=CTX, now=NOW)
    assert not Order.objects.exists()


def test_an_order_older_than_ten_years_is_not_migrated(vendors: dict[str, Any]) -> None:
    with pytest.raises(legacy.LegacyRecordError, match="10-year"):
        archive.load_archive_order(
            record(created_at="2015-03-10T09:30:00+00:00"), vendors=vendors, ctx=CTX, now=NOW
        )
    assert not Order.objects.exists()


def test_an_unknown_vendor_key_is_refused(vendors: dict[str, Any]) -> None:
    rec = record()
    rec["vendors"][0]["vendor_key"] = "Z"
    with pytest.raises(legacy.LegacyRecordError, match="unknown vendor"):
        archive.load_archive_order(rec, vendors=vendors, ctx=CTX, now=NOW)


def test_a_failed_load_takes_no_number(vendors: dict[str, Any]) -> None:
    with pytest.raises(legacy.LegacyRecordError):
        archive.load_archive_order(record(tax_total="9.99"), vendors=vendors, ctx=CTX, now=NOW)
    assert load(record(4713), vendors).number == "2026-000001"


def test_a_dry_run_saves_nothing_and_lists_the_creations(vendors: dict[str, Any]) -> None:
    order, changeset = archive.load_archive_order(
        record(), vendors=vendors, ctx=CTX, now=NOW, dry_run=True
    )
    assert order is None
    assert changeset.summary()["create"] == 3  # order, vendor part, line
    assert not Order.objects.exists()
    assert not AuditLog.objects.filter(action="orders.create").exists()


def test_the_load_is_audited_without_personal_data(vendors: dict[str, Any]) -> None:
    load(record(), vendors)
    rows = AuditLog.objects.filter(action="orders.create", target_model="orders.order")
    assert rows.count() == 1
    assert "Mario" not in repr(rows.get().after)


def test_the_database_keeps_the_archive_flag_consistent(vendors: dict[str, Any]) -> None:
    order = load(record(), vendors)
    with pytest.raises(IntegrityError), connection.cursor() as cursor:
        cursor.execute(
            "UPDATE orders_order SET legacy_wp_order_id = NULL WHERE id = %s", [order.pk]
        )


# --- visibility ---------------------------------------------------------------------------------
# Per platform role: apps/manager/tests/integration/test_tax_archive_role.py (orders never imports
# the manager).


def test_a_normal_order_is_not_in_the_archive(vendors: dict[str, Any]) -> None:
    from apps.orders.tests.factories import OrderFactory

    OrderFactory.create()
    load(record(), vendors)
    assert archive.archive_orders(["tax_audit"]).count() == 1
    assert not archive.archive_orders([]).exists()


# --- retention -----------------------------------------------------------------------------------


def _expired(vendors: dict[str, Any]) -> Order:
    """An order of March 2015, loaded while still inside its retention (until end of 2025)."""
    rec = record(created_at="2015-03-10T09:30:00+00:00", paid_at="2015-03-10T09:35:00+00:00")
    order, _ = archive.load_archive_order(
        rec, vendors=vendors, ctx=CTX, now=dt.datetime(2024, 6, 1, tzinfo=dt.UTC)
    )
    assert order is not None
    return order


def test_an_archive_order_inside_its_ten_years_is_kept(vendors: dict[str, Any]) -> None:
    order = load(record(), vendors)
    assert archive.purge_expired_archive(ctx=CTX, now=NOW).is_empty
    order.refresh_from_db()
    assert order.privacy_status == "restricted_tax_only"
    assert order.customer_name == "Mario Rossi"


def test_the_day_after_the_retention_date_the_personal_data_goes(
    vendors: dict[str, Any],
) -> None:
    order = _expired(vendors)
    assert order.tax_retention_until == dt.date(2025, 12, 31)
    on_the_day = dt.datetime(2025, 12, 31, 12, 0, tzinfo=dt.UTC)
    assert archive.purge_expired_archive(ctx=CTX, now=on_the_day).is_empty
    order.refresh_from_db()
    assert order.customer_name == "Mario Rossi"
    after = dt.datetime(2026, 1, 2, 12, 0, tzinfo=dt.UTC)
    changeset = archive.purge_expired_archive(ctx=CTX, now=after)
    assert changeset.summary()["update"] == 1
    order.refresh_from_db()
    assert order.privacy_status == "anonymized"
    assert (order.customer_name, order.customer_vat_number) == ("ANONYMISED", "ANONYMISED")
    assert order.billing_address == {"anonymised": True}
    assert order.grand_total == Decimal("30.50")  # amounts stay
    assert AuditLog.objects.filter(action="orders.anonymise").count() == 1


def test_the_retention_dry_run_changes_nothing_and_lists_the_orders(
    vendors: dict[str, Any],
) -> None:
    order = _expired(vendors)
    after = dt.datetime(2026, 1, 2, 12, 0, tzinfo=dt.UTC)
    changeset = archive.purge_expired_archive(ctx=CTX, now=after, dry_run=True)
    assert changeset.summary()["update"] == 1
    order.refresh_from_db()
    assert order.privacy_status == "restricted_tax_only"
    assert order.customer_name == "Mario Rossi"
    assert not AuditLog.objects.filter(action="orders.anonymise").exists()


def test_the_retention_job_is_idempotent_and_leaves_active_orders_alone(
    vendors: dict[str, Any],
) -> None:
    _expired(vendors)
    recent = load(record(4800), vendors)
    after = dt.datetime(2026, 1, 2, 12, 0, tzinfo=dt.UTC)
    archive.purge_expired_archive(ctx=CTX, now=after)
    assert archive.purge_expired_archive(ctx=CTX, now=after).is_empty
    recent.refresh_from_db()
    assert recent.privacy_status == "restricted_tax_only"
