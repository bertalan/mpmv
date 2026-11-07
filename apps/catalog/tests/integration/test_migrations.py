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
