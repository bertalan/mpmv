"""CAT.1 and CAT.13: the catalog migrations apply and reverse cleanly, raw SQL included."""

from typing import Any

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client

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


def _d25_migration() -> Any:
    import importlib

    return importlib.import_module("apps.catalog.migrations.0010_d25_product_slugs")


@pytest.mark.django_db
def test_d25_migration_dry_run_changes_nothing() -> None:
    """CAT.5b: a dry run lists the reslugs and writes neither slugs, history nor audit."""
    from django.apps import apps

    from apps.catalog.models import SlugHistory
    from apps.catalog.tests.factories import ProductTranslationFactory
    from apps.core.models import AuditLog

    translation = ProductTranslationFactory.create(
        language="en", title="Leather Bag", slug="leather-bag-en"
    )
    changes = _d25_migration().reslug_products(apps, None, dry_run=True)
    assert changes == [(translation.pk, "leather-bag-en", "leather-bag")]
    translation.refresh_from_db()
    assert translation.slug == "leather-bag-en"
    assert not SlugHistory.objects.exists()
    assert not AuditLog.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_d25_migration_reslugs_both_legacy_shapes_on_the_historical_state(
    client: Client,
) -> None:
    """CAT.5b: `{title}-{lang}` and `{title}-{lang}-{sku}` slugs end as history with one 301."""
    from django.apps import apps as django_apps

    from apps.catalog.models import ProductTranslation, SlugHistory
    from apps.core.models import AuditLog

    before = [("catalog", "0009_redirect")]
    executor = MigrationExecutor(connection)
    executor.migrate(before)
    old = executor.loader.project_state(before).apps
    vendor = VendorFactory.create()
    product_model = old.get_model("catalog", "Product")
    translation_model = old.get_model("catalog", "ProductTranslation")
    history_model = old.get_model("catalog", "SlugHistory")
    first = product_model.objects.create(
        vendor_id=vendor.pk, sku="BAG-1", sku_key="bag-1", price_net="1.00"
    )
    second = product_model.objects.create(
        vendor_id=vendor.pk, sku="BAG-2", sku_key="bag-2", price_net="1.00"
    )
    translation_model.objects.create(
        product=first, language="en", title="Leather Bag", slug="leather-bag-en"
    )
    translation_model.objects.create(
        product=second, language="en", title="Leather Bag", slug="leather-bag-en-bag-2"
    )
    history_model.objects.create(product=first, language="en", slug="leather-bag-en-bag-1")

    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())

    slugs = dict(ProductTranslation.objects.values_list("product__sku", "slug"))
    assert slugs == {"BAG-1": "leather-bag", "BAG-2": "leather-bag-2"}
    assert sorted(SlugHistory.objects.values_list("slug", flat=True)) == [
        "leather-bag-en",
        "leather-bag-en-bag-1",
        "leather-bag-en-bag-2",
    ]
    entries = AuditLog.objects.filter(action="catalog.product.slug_changed")
    assert entries.count() == 2
    assert {entry.source for entry in entries} == {"command"}
    for old_slug, new_slug in (
        ("leather-bag-en", "leather-bag"),
        ("leather-bag-en-bag-1", "leather-bag"),
        ("leather-bag-en-bag-2", "leather-bag-2"),
    ):
        response = client.get(f"/product/{old_slug}/")
        assert response.status_code == 301
        assert response["Location"] == f"/product/{new_slug}/"

    # A rerun changes nothing.
    assert _d25_migration().reslug_products(django_apps, None) == []


@pytest.mark.parametrize(
    "title",
    ["Leather Bag", "Borsa in pelle è più bella", "Große Tasche", "Łódź & Æble", "★☆", "x" * 120],
)
def test_the_frozen_slug_copy_matches_slugs_py_today(title: str) -> None:
    """The migration keeps a private copy of the slug rules; a change in slugs.py must be
    decided on purpose, so the copy is pinned against it."""
    from apps.catalog import slugs

    frozen = _d25_migration()
    assert frozen.unique_product_slug(title, taken=set()) == slugs.unique_product_slug(
        title, taken=set()
    )
    taken = {slugs.build_product_slug(title)}
    assert frozen.unique_product_slug(title, taken=taken) == slugs.unique_product_slug(
        title, taken=taken
    )


@pytest.mark.django_db
def test_d25_migration_is_a_noop_without_translations() -> None:
    from django.apps import apps

    assert _d25_migration().reslug_products(apps, None) == []


@pytest.mark.django_db
def test_d25_migration_first_slug_has_no_history_or_audit_and_bumps_updated_at() -> None:
    from django.apps import apps

    from apps.catalog.models import SlugHistory
    from apps.catalog.tests.factories import ProductTranslationFactory
    from apps.core.models import AuditLog

    translation = ProductTranslationFactory.create(language="en", title="Safari", slug="")
    before = translation.updated_at
    _d25_migration().reslug_products(apps, None)
    translation.refresh_from_db()
    assert translation.slug == "safari"
    assert translation.updated_at > before
    assert not SlugHistory.objects.exists()
    assert not AuditLog.objects.exists()


@pytest.mark.django_db
def test_d25_migration_reclaims_an_own_old_slug_without_a_loop() -> None:
    from django.apps import apps

    from apps.catalog.models import SlugHistory
    from apps.catalog.tests.factories import ProductTranslationFactory

    translation = ProductTranslationFactory.create(
        language="en", title="Leather Bag", slug="travel-bag"
    )
    SlugHistory.objects.create(product=translation.product, language="en", slug="leather-bag")
    _d25_migration().reslug_products(apps, None)
    translation.refresh_from_db()
    assert translation.slug == "leather-bag"
    assert list(SlugHistory.objects.values_list("slug", flat=True)) == ["travel-bag"]
