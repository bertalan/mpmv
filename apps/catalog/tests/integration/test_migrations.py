"""CAT.1: the catalog migrations apply and reverse cleanly, raw SQL constraints included."""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


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
        "apps.catalog.migrations.0006_variant_override_sale_positive"
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
