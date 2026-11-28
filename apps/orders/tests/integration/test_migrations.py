"""ORD.2: the order guard migrations reverse cleanly, leaving working triggers. ORD.29: the
split-shipping migration keeps an existing order exactly."""

import importlib
import re
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
# the state labels (I18N.10 follow-up): no column. 0024 (VEN.20) adds a nullable date that the
# current model writes, so the test lends the 0020 table that column while it creates the order.
# 0025 (ORD.41) only replaces the order guard function: no column.
CHECKED_LEAF = "0025_ord41_trigger_time_zone"
LATER_COLUMN = "expected_dispatch_on"
TRIGGER_ZONE = "0025_ord41_trigger_time_zone"
BEFORE_TRIGGER_ZONE = "0024_ven20_expected_dispatch_on"
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


def _later_column(*, add: bool) -> None:
    """Add (or drop again) the column a later migration brings, on the older schema."""
    with connection.cursor() as cursor:
        if add:
            cursor.execute(f"ALTER TABLE orders_vendororder ADD COLUMN {LATER_COLUMN} date NULL")
        else:
            cursor.execute(f"ALTER TABLE orders_vendororder DROP COLUMN IF EXISTS {LATER_COLUMN}")


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
        _later_column(add=True)
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
        _later_column(add=False)
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
        _later_column(add=False)
        migrate(latest)


def _guard_source() -> str:
    with connection.cursor() as cursor:
        cursor.execute("SELECT prosrc FROM pg_proc WHERE proname = 'orders_order_guard'")
        [(source,)] = cursor.fetchall()
    return str(source)


def _body_of_the_0010_guard() -> str:
    """The body (`prosrc`) of the guard that migration 0025 restores on reverse."""
    migration = importlib.import_module(f"apps.orders.migrations.{TRIGGER_ZONE}")
    match = re.search(r"AS \$\$(.*)\$\$ LANGUAGE plpgsql;", migration._guard_0010(), re.S)
    assert match is not None
    return match.group(1)


def _zone_function_exists() -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM pg_proc WHERE proname = 'business_time_zone_at'")
        row = cursor.fetchone()
    return bool(row == (1,))


@pytest.mark.django_db(transaction=True)
def test_the_trigger_zone_migration_reverses_to_the_frozen_zone_and_forward_again() -> None:
    """ORD.41: back to 0024 the guard is exactly 0010's function again, frozen zone included;
    core back to 0035 drops `business_time_zone_at`; forward, both read the operator."""
    latest = _orders_leaf()
    executor = MigrationExecutor(connection)
    try:
        migrate(BEFORE_TRIGGER_ZONE)
        assert _guard_source() == _body_of_the_0010_guard()
        assert "AT TIME ZONE 'Europe/Rome'" in _guard_source()
        executor.loader.build_graph()
        executor.migrate([("core", "0035_platformoperator_business_time_zone")])
        assert not _zone_function_exists()
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
    assert latest == TRIGGER_ZONE
    assert _zone_function_exists()
    source = _guard_source()
    assert "AT TIME ZONE '" not in source
    assert source.count("business_time_zone_at(") == 3
