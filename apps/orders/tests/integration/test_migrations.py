"""ORD.2: the order guard migrations reverse cleanly, leaving working triggers. ORD.29: the
split-shipping migration keeps an existing order exactly."""

from decimal import Decimal

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader

from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.orders import services
from apps.orders.services import LineInput, VendorOrderInput
from apps.vendors.tests.factories import VendorFactory

SPLIT_SHIPPING = "0021_ord29_split_shipping_vat"
BEFORE_SPLIT = "0020_ord20_order_events"
# The leaf this test was last checked against: ORD.32 (0022) only adds the staff draft tables,
# which `create_order` never writes, so creating an order at 0020 still works. 0023 only changes
# the state labels (I18N.10 follow-up): no column.
CHECKED_LEAF = "0023_alter_order_state_alter_orderamendment_state"
TOTALS = (
    "SELECT goods_net, shipping_net, tax_total, gross_total, shipping_tax_rate,"
    " shipping_tax_amount, shipping_tax_reason FROM orders_vendororder ORDER BY id"
)
COMMISSION = {
    "rule_id": "default",
    "version": 1,
    "percent": "10.00",
    "base": "goods",
    "fee_percent": "0",
    "fee_fixed": "0",
    "fee_base": "gross",
}


def _orders_leaf() -> str:
    leaves = MigrationLoader(connection).graph.leaf_nodes("orders")
    assert len(leaves) == 1, f"orders has several migration leaves: {leaves}"
    return str(leaves[0][1])


def migrate(target: str) -> None:
    executor = MigrationExecutor(connection)
    executor.migrate([("orders", target)])


@pytest.mark.django_db(transaction=True)
def test_migrating_back_to_0003_leaves_working_guards_and_forward_again_works() -> None:
    latest = _orders_leaf()
    try:
        migrate("0003_database_guards")
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO orders_order (public_id, created_at, updated_at, number, email,"
                " language, billing_address, billing_country, shipping_address,"
                " shipping_country, customer_tax_status, vat_validation, state, goods_net,"
                " shipping_net, tax_total, grand_total, payment_intent_ref, transfer_group)"
                " VALUES (gen_random_uuid(), now(), now(), '2998-000301', 'a@example.com', 'en',"
                " '{}', 'IT', '{}', 'IT', 'b2c', '{}', 'checkout', 0, 0, 0, 0, '', '')"
            )
            cursor.execute("UPDATE orders_order SET state = 'awaiting_payment'")
            cursor.execute("SELECT count(*) FROM orders_order")
            assert cursor.fetchone() == (1,)
    finally:
        migrate(latest)


def _totals() -> list[tuple[object, ...]]:
    with connection.cursor() as cursor:
        cursor.execute(TOTALS)
        return list(cursor.fetchall())


@pytest.mark.django_db(transaction=True)
def test_the_split_shipping_migration_keeps_an_existing_order_exactly() -> None:
    """ORD.29: the migration only relaxes two columns and one check. An order placed before it
    keeps its single rate, its shipping VAT and its totals to the cent (no backfill: every
    existing row is single-rate)."""
    latest = _orders_leaf()
    assert latest == CHECKED_LEAF, f"update this test for the new orders leaf {latest}"
    line = LineInput(
        None,
        "SKU-1",
        "Cup",
        Decimal("50.00"),
        2,
        Decimal("22"),
        "domestic",
    )
    try:
        migrate(BEFORE_SPLIT)
        order, _ = services.create_order(
            email="buyer@example.com",
            language="en",
            billing_address={},
            billing_country="IT",
            shipping_address={},
            shipping_country="IT",
            vendor_orders=[
                VendorOrderInput(
                    vendor=VendorFactory.create(),
                    lines=[line],
                    shipping_net=Decimal("9.90"),
                    shipping_tax_rate=Decimal("22"),
                    shipping_tax_reason="domestic",
                    commission_snapshot=COMMISSION,
                )
            ],
            ctx=AuditContext(actor_label="system:test", source=AuditLog.Source.SYSTEM),
        )
        assert order is not None
        before = _totals()
        migrate(SPLIT_SHIPPING)
        after = _totals()
        assert before == [
            (
                Decimal("100.00"),
                Decimal("9.90"),
                Decimal("24.18"),
                Decimal("134.08"),
                Decimal("22.000"),
                Decimal("2.18"),
                "domestic",
            )
        ]
        assert after == before, f"the migration changed an existing order: {before} -> {after}"
    finally:
        migrate(latest)
