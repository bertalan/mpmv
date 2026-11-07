"""CAT.13: internal identity and the vendor SKU as a label (spec 03 §3.2 and §3.3).

Owner decisions Q-30 and Q-33: the platform identifies products and variants by `public_id`
(and a staff-only `staff_ref`); the vendor SKU is a free-text label stored raw, matched on its
match key, unique per vendor across products and variants together, with a `SkuAlias` history
so that renamed SKUs still match older import files.
"""

import re
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.forms import modelform_factory

from apps.catalog import engine
from apps.catalog.models import Product, SkuAlias, Variant
from apps.catalog.services import (
    AmbiguousSkuMatch,
    SkuRow,
    match_skus,
    rename_product_sku,
    rename_variant_sku,
    update_by_sku,
)
from apps.catalog.tests.factories import ProductFactory, VariantFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:catalog", source="admin", ip="127.0.0.1")


def refused(**fields: Any) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        ProductFactory.create(**fields)


def variable(vendor: Any) -> Product:
    return ProductFactory.create(vendor=vendor, kind=Product.Kind.VARIABLE)


# --- §3.3 regression cases


def test_sku_space() -> None:
    """SKU-SPACE: `AB 100` is stored raw; `ab  100 ` has the same match key and is refused."""
    vendor = VendorFactory.create()
    first = ProductFactory.create(vendor=vendor, sku="AB 100")
    first.refresh_from_db()
    assert (first.sku, first.sku_key) == ("AB 100", "ab 100")

    second = ProductFactory.build(vendor=vendor, sku="ab  100 ")
    with pytest.raises(ValidationError) as exc:
        second.full_clean()
    assert "sku" in exc.value.message_dict
    refused(vendor=vendor, sku="ab  100 ")


def test_sku_slash() -> None:
    """SKU-SLASH: `BAG/100` is accepted with a warning; slugs never carry the SKU."""
    product = ProductFactory.create(sku="BAG/100", languages=["en"])
    product.full_clean()
    product.refresh_from_db()
    assert product.sku == "BAG/100"
    assert engine.sku_warnings(product.sku)
    # Slugs are derived from the title and language only (CAT.5); never from the SKU.
    for translation in product.translations.all():
        assert product.sku_key not in translation.slug
        assert "/" not in translation.slug


def test_sku_case() -> None:
    """SKU-CASE: a variant `BAG-100` is refused when product `Bag-100` exists (one namespace)."""
    vendor = VendorFactory.create()
    ProductFactory.create(vendor=vendor, sku="Bag-100")
    parent = variable(vendor)

    with pytest.raises(ValidationError) as exc:
        VariantFactory.build(product=parent, sku="BAG-100").full_clean()
    assert "This vendor already has a product with this SKU." in str(exc.value)

    with pytest.raises(IntegrityError), transaction.atomic():
        VariantFactory.create(product=parent, sku="BAG-100")


def test_sku_case_product_after_variant() -> None:
    vendor = VendorFactory.create()
    VariantFactory.create(product=variable(vendor), sku="BAG-100")

    with pytest.raises(ValidationError) as exc:
        ProductFactory.build(vendor=vendor, sku="bag-100").full_clean()
    assert "This vendor already has a variant with this SKU." in str(exc.value)
    refused(vendor=vendor, sku="bag-100")


def test_sku_trim() -> None:
    """SKU-TRIM: ` BAG-100 ` is kept raw with a warning; the match key is `bag-100`."""
    product = ProductFactory.create(sku=" BAG-100 ")
    product.refresh_from_db()
    assert (product.sku, product.sku_key) == (" BAG-100 ", "bag-100")
    assert engine.sku_warnings(product.sku)


def test_sku_rename() -> None:
    """SKU-RENAME: after `BAG-100` → `BAG-100A`, an import row for `BAG-100` updates the same
    product through `SkuAlias`."""
    vendor = VendorFactory.create()
    product = ProductFactory.create(vendor=vendor, sku="BAG-100", stock_qty=1)
    rename_product_sku(product, "BAG-100A", ctx=CTX)

    alias = SkuAlias.objects.get()
    assert (alias.vendor, alias.product, alias.variant) == (vendor, product, None)
    assert (alias.sku, alias.sku_key) == ("BAG-100", "bag-100")
    assert alias.replaced_at is not None

    assert match_skus(vendor, ["BAG-100"]) == {"BAG-100": product}
    update_by_sku(vendor, [SkuRow("BAG-100", {"stock_qty": 7})], ctx=CTX)
    product.refresh_from_db()
    assert (product.sku, product.stock_qty) == ("BAG-100A", 7)


def test_sku_ambiguous() -> None:
    """SKU-AMBIGUOUS: `BAG-100` renamed `BAG-200`, a new product takes `BAG-100`: the import
    stops with a report naming both items and writes nothing."""
    vendor = VendorFactory.create()
    renamed = ProductFactory.create(vendor=vendor, sku="BAG-100", stock_qty=1)
    rename_product_sku(renamed, "BAG-200", ctx=CTX)
    newcomer = ProductFactory.create(vendor=vendor, sku="BAG-100", stock_qty=1)
    audit_entries = AuditLog.objects.count()

    with pytest.raises(AmbiguousSkuMatch) as exc:
        update_by_sku(vendor, [SkuRow("BAG-100", {"stock_qty": 9})], ctx=CTX)

    report = str(exc.value)
    assert "BAG-100" in report
    assert str(renamed.public_id) in report
    assert "BAG-200" in report
    assert str(newcomer.public_id) in report
    assert set(exc.value.candidates["BAG-100"]) == {renamed, newcomer}
    for product in (renamed, newcomer):
        product.refresh_from_db()
        assert product.stock_qty == 1
    assert AuditLog.objects.count() == audit_entries


def test_sku_two_vendors() -> None:
    """SKU-TWO-VENDORS: both vendors may use `BAG-100`; an import for B never touches A."""
    vendor_a, vendor_b = VendorFactory.create(), VendorFactory.create()
    product_a = ProductFactory.create(vendor=vendor_a, sku="BAG-100", stock_qty=1)
    product_b = ProductFactory.create(vendor=vendor_b, sku="BAG-100", stock_qty=1)
    VariantFactory.create(product=variable(vendor_a), sku="BAG-101")

    update_by_sku(vendor_b, [SkuRow("bag-100", {"stock_qty": 5})], ctx=CTX)
    product_a.refresh_from_db()
    product_b.refresh_from_db()
    assert (product_a.stock_qty, product_b.stock_qty) == (1, 5)
    assert match_skus(vendor_b, ["BAG-101"]) == {"BAG-101": None}


# --- One namespace per vendor, enforced by the database


def raw_sql(sql: str, params: list[Any]) -> None:
    with pytest.raises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(sql, params)


def test_raw_sql_cannot_give_a_variant_the_match_key_of_a_product() -> None:
    vendor = VendorFactory.create()
    ProductFactory.create(vendor=vendor, sku="BAG-100")
    variant = VariantFactory.create(product=variable(vendor), sku="BAG-RED")
    raw_sql("UPDATE catalog_variant SET sku_key = 'bag-100' WHERE id = %s", [variant.pk])


def test_raw_sql_cannot_give_a_product_the_match_key_of_a_variant() -> None:
    vendor = VendorFactory.create()
    VariantFactory.create(product=variable(vendor), sku="BAG-RED")
    product = ProductFactory.create(vendor=vendor, sku="BAG-100")
    raw_sql("UPDATE catalog_product SET sku_key = 'bag-red' WHERE id = %s", [product.pk])


def test_moving_a_product_to_a_vendor_using_its_key_is_refused() -> None:
    other = VendorFactory.create()
    VariantFactory.create(product=variable(other), sku="BAG-100")
    product = ProductFactory.create(sku="BAG-100")
    product.vendor = other
    with pytest.raises(IntegrityError), transaction.atomic():
        product.save()


def test_a_variant_may_reuse_a_key_of_its_own_product_after_the_product_is_renamed() -> None:
    vendor = VendorFactory.create()
    parent = ProductFactory.create(vendor=vendor, kind=Product.Kind.VARIABLE, sku="BAG-100")
    rename_product_sku(parent, "BAG", ctx=CTX)
    variant = VariantFactory.create(product=parent, sku="BAG-100")
    assert variant.sku_key == "bag-100"


# --- Renames: audited service, alias history


def test_rename_dry_run_writes_nothing() -> None:
    product = ProductFactory.create(sku="BAG-100")
    changes = rename_product_sku(product, "BAG-100A", ctx=CTX, dry_run=True)
    assert changes.summary() == {"create": 1, "update": 1, "delete": 0}
    product.refresh_from_db()
    assert product.sku == "BAG-100"
    assert not SkuAlias.objects.exists()
    assert not AuditLog.objects.exists()


def test_rename_is_audited_with_before_and_after() -> None:
    product = ProductFactory.create(sku="BAG-100")
    rename_product_sku(product, "BAG-100A", ctx=CTX)
    assert product.sku == "BAG-100A"
    entry = AuditLog.objects.get(target_model="catalog.product")
    assert entry.action == "catalog.product.sku_renamed"
    assert entry.target_key == str(product.public_id)
    assert (entry.before or {})["sku"] == "BAG-100"
    assert (entry.after or {})["sku"] == "BAG-100A"
    assert "staff_ref" not in (entry.before or {})


def test_rename_to_a_taken_key_is_refused() -> None:
    vendor = VendorFactory.create()
    VariantFactory.create(product=variable(vendor), sku="BAG-RED")
    product = ProductFactory.create(vendor=vendor, sku="BAG-100")
    with pytest.raises(ValidationError) as exc:
        rename_product_sku(product, "bag-red", ctx=CTX)
    assert "This vendor already has a variant with this SKU." in str(exc.value)


def test_rename_to_an_empty_sku_is_refused() -> None:
    product = ProductFactory.create(sku="BAG-100")
    with pytest.raises(ValidationError):
        rename_product_sku(product, "   ", ctx=CTX)


def test_a_raw_only_change_keeps_the_key_and_writes_no_alias() -> None:
    product = ProductFactory.create(sku="BAG-100")
    rename_product_sku(product, "bag-100", ctx=CTX)
    product.refresh_from_db()
    assert (product.sku, product.sku_key) == ("bag-100", "bag-100")
    assert not SkuAlias.objects.exists()


def test_a_variant_rename_writes_an_alias_and_still_matches() -> None:
    vendor = VendorFactory.create()
    variant = VariantFactory.create(product=variable(vendor), sku="BAG-RED")
    rename_variant_sku(variant, "BAG-ROUGE", ctx=CTX)
    alias = SkuAlias.objects.get()
    assert (alias.variant, alias.product, alias.sku_key) == (variant, None, "bag-red")
    assert match_skus(vendor, ["bag-red", "BAG-ROUGE"]) == {
        "bag-red": variant,
        "BAG-ROUGE": variant,
    }
    entry = AuditLog.objects.get(target_model="catalog.variant")
    assert entry.action == "catalog.variant.sku_renamed"


def test_any_sku_change_writes_an_alias_even_outside_the_service() -> None:
    product = ProductFactory.create(sku="BAG-100")
    product.sku = "BAG-300"
    product.save()
    assert SkuAlias.objects.get().sku == "BAG-100"


def test_deleting_a_product_deletes_its_aliases() -> None:
    product = ProductFactory.create(sku="BAG-100")
    rename_product_sku(product, "BAG-101", ctx=CTX)
    product.delete()
    assert not SkuAlias.objects.exists()


def test_an_unknown_sku_stops_the_update_and_writes_nothing() -> None:
    vendor = VendorFactory.create()
    product = ProductFactory.create(vendor=vendor, sku="BAG-100", stock_qty=1)
    with pytest.raises(ValidationError):
        update_by_sku(
            vendor,
            [SkuRow("BAG-100", {"stock_qty": 4}), SkuRow("NOPE-1", {"stock_qty": 4})],
            ctx=CTX,
        )
    product.refresh_from_db()
    assert product.stock_qty == 1


def test_update_by_sku_refuses_fields_outside_its_list() -> None:
    vendor = VendorFactory.create()
    ProductFactory.create(vendor=vendor, sku="BAG-100")
    with pytest.raises(ValidationError):
        update_by_sku(vendor, [SkuRow("BAG-100", {"price_net": "1.00"})], ctx=CTX)


def test_update_by_sku_dry_run_writes_nothing() -> None:
    vendor = VendorFactory.create()
    product = ProductFactory.create(vendor=vendor, sku="BAG-100", stock_qty=1)
    changes = update_by_sku(vendor, [SkuRow("BAG-100", {"stock_qty": 3})], ctx=CTX, dry_run=True)
    assert changes.summary() == {"create": 0, "update": 1, "delete": 0}
    product.refresh_from_db()
    assert product.stock_qty == 1


# --- staff_ref: a staff-only sequence


def test_products_and_variants_get_a_prefixed_staff_ref() -> None:
    product = ProductFactory.create()
    variant = VariantFactory.create()
    assert re.fullmatch(r"P-\d{6,}", product.staff_ref)
    assert re.fullmatch(r"V-\d{6,}", variant.staff_ref)
    second = ProductFactory.create()
    assert int(second.staff_ref[2:]) > int(product.staff_ref[2:])
    product.refresh_from_db()
    assert re.fullmatch(r"P-\d{6,}", product.staff_ref)


def test_staff_ref_is_unique_and_never_a_form_field() -> None:
    first, second = ProductFactory.create(), ProductFactory.create()
    with pytest.raises(IntegrityError), transaction.atomic():
        Product.objects.filter(pk=second.pk).update(staff_ref=first.staff_ref)
    assert "staff_ref" not in modelform_factory(Product, fields="__all__").base_fields
    assert "staff_ref" not in modelform_factory(Variant, fields="__all__").base_fields


def test_the_api_schema_never_exposes_staff_ref(client: Any) -> None:
    schema = client.get("/api/v1/openapi.json")
    assert schema.status_code == 200
    assert "staff_ref" not in schema.content.decode()


def test_the_vendor_facing_label_is_the_sku_not_the_staff_ref() -> None:
    product = ProductFactory.create(sku="BAG-100")
    assert str(product) == "BAG-100"
    assert product.staff_ref not in str(product)
