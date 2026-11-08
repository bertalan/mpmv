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


@pytest.mark.django_db
def test_the_zero_price_migration_lists_the_rows_it_would_refuse() -> None:
    import importlib

    from django.apps import apps

    from apps.catalog.tests.factories import ProductFactory, VariantFactory

    migration = importlib.import_module(
        "apps.catalog.migrations.0007_variant_override_sale_positive"
    )
    with connection.cursor() as cursor:  # rolled back with the test transaction
        cursor.execute(
            "ALTER TABLE catalog_variant DROP CONSTRAINT catalog_variant_price_override_positive"
        )
        cursor.execute(
            "ALTER TABLE catalog_product DROP CONSTRAINT catalog_product_sale_price_below_price"
        )
    variant = VariantFactory.create(price_net_override=0)
    product = ProductFactory.create(sale_price_net=0)
    with pytest.raises(RuntimeError) as error:
        migration.refuse_zero_prices(apps, None)
    assert variant.sku in str(error.value)
    assert product.sku in str(error.value)


@pytest.mark.django_db(transaction=True)
def test_cat13_migration_fills_match_keys_and_staff_refs_of_existing_rows() -> None:
    """CAT.13: rows saved before the migration get a match key and a staff_ref."""
    before = [("catalog", "0005_slug_history")]
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
