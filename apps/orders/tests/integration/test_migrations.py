"""ORD.2: the order guard migrations reverse cleanly, leaving working triggers."""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader


def _orders_leaf() -> str:
    leaves = MigrationLoader(connection).graph.leaf_nodes("orders")
    assert len(leaves) == 1, f"orders has several migration leaves: {leaves}"
    return leaves[0][1]


LATEST = _orders_leaf()


def migrate(target: str) -> None:
    executor = MigrationExecutor(connection)
    executor.migrate([("orders", target)])


@pytest.mark.django_db(transaction=True)
def test_migrating_back_to_0003_leaves_working_guards_and_forward_again_works() -> None:
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
        migrate(LATEST)
