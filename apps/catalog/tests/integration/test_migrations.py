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


def _d25_migration():  # type: ignore[no-untyped-def]
    import importlib

    return importlib.import_module("apps.catalog.migrations.0010_d25_product_slugs")


@pytest.mark.django_db
def test_d25_migration_dry_run_changes_nothing() -> None:
    """CAT.5b: a dry run lists the reslugs and writes neither slugs, history nor audit."""
    from django.apps import apps

    from apps.catalog.models import ProductTranslation, SlugHistory
    from apps.catalog.tests.factories import ProductTranslationFactory
    from apps.core.models import AuditLog

    translation = ProductTranslationFactory.create(
        language="en", title="Leather Bag", slug="leather-bag-en"
    )
    changes = _d25_migration().reslug_products(apps, None, dry_run=True)
    assert not changes.is_empty
    translation.refresh_from_db()
    assert translation.slug == "leather-bag-en"
    assert not SlugHistory.objects.exists()
    assert not AuditLog.objects.exists()
    assert ProductTranslation.objects.count() == 1


@pytest.mark.django_db
def test_d25_migration_reslugs_existing_products_with_history_and_audit() -> None:
    """CAT.5b: `{title}-{lang}` slugs move to history, audited; a rerun changes nothing."""
    from django.apps import apps

    from apps.catalog.models import SlugHistory
    from apps.catalog.tests.factories import ProductTranslationFactory
    from apps.core.models import AuditLog

    translation = ProductTranslationFactory.create(
        language="it", title="Borsa in pelle", slug="borsa-in-pelle-it"
    )
    migration = _d25_migration()
    migration.reslug_products(apps, None)
    translation.refresh_from_db()
    assert translation.slug == "borsa-in-pelle"
    assert list(SlugHistory.objects.values_list("slug", flat=True)) == ["borsa-in-pelle-it"]
    entry = AuditLog.objects.get(action="catalog.product.slug_changed")
    assert entry.source == "command"
    assert migration.reslug_products(apps, None).is_empty
    assert SlugHistory.objects.count() == 1
