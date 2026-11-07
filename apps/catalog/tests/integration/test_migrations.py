"""CAT.1 and CAT.13: the catalog migrations apply and reverse cleanly, raw SQL included."""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from apps.vendors.tests.factories import VendorFactory


def variant_foreign_keys() -> set[tuple[str, ...]]:
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, "catalog_variant")
    return {tuple(c["columns"]) for c in constraints.values() if c["foreign_key"]}


@pytest.mark.django_db(transaction=True)
def test_catalog_migrations_are_reversible() -> None:
    assert ("product_id", "vendor_id") in variant_foreign_keys()

    executor = MigrationExecutor(connection)
    executor.migrate([("catalog", None)])
    assert "catalog_product" not in connection.introspection.table_names()

    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())
    assert "catalog_product" in connection.introspection.table_names()
    assert ("product_id", "vendor_id") in variant_foreign_keys()


@pytest.mark.django_db(transaction=True)
def test_cat13_migration_fills_match_keys_and_staff_refs_of_existing_rows() -> None:
    """CAT.13: rows saved before the migration get a match key and a staff_ref."""
    before = [("catalog", "0002_taxonomies")]
    executor = MigrationExecutor(connection)
    executor.migrate(before)
    old = executor.loader.project_state(before).apps
    vendor = VendorFactory.create()  # the vendors app is not rolled back
    product = old.get_model("catalog", "Product").objects.create(
        vendor_id=vendor.pk, sku="BAG 100", price_net="1.00"
    )

    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT sku, sku_key, staff_ref FROM catalog_product WHERE id = %s", [product.pk]
        )
        assert cursor.fetchone() == ("BAG 100", "bag 100", "P-000001")
