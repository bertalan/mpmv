"""CAT.1: Product and Variant constraints against the real database (spec 03 §3.2).

The database is the last line of defence: every rule here also holds for `QuerySet.update()`,
bulk imports and raw SQL, not only for forms.
"""

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError

from apps.catalog.models import Product, ProductTranslation, Variant, VariantTranslation
from apps.catalog.tests.factories import (
    ProductFactory,
    ProductTranslationFactory,
    VariantFactory,
    VariantTranslationFactory,
)
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

JAN = dt.datetime(2027, 1, 1, tzinfo=dt.UTC)
FEB = dt.datetime(2027, 2, 1, tzinfo=dt.UTC)


def refused_by_the_database(**fields: Any) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        ProductFactory.create(**fields)


# --- Product defaults and SKU


def test_new_product_defaults() -> None:
    vendor = VendorFactory.create()
    product = Product.objects.create(  # model defaults under test: no factory values
        vendor=vendor, sku="SKU-1", price_net=Decimal("10.00")
    )
    assert product.kind == Product.Kind.SIMPLE
    assert product.status == Product.Status.DRAFT
    assert product.sales_mode == Product.SalesMode.BUY
    assert product.fiscal_category == Product.FiscalCategory.STANDARD
    assert product.stock_policy == Product.StockPolicy.TRACK
    assert product.stock_status == Product.StockStatus.IN_STOCK
    assert product.stock_qty == 0
    assert product.sale_price_net is None
    assert product.weight_kg is None
    assert product.gtin == ""
    assert product.legacy_wp_id_en is None
    assert product.public_id is not None
    assert str(product) == "SKU-1"


def test_sku_is_stored_upper_case_and_stripped() -> None:
    product = ProductFactory.create(sku="  abc-123 ")
    assert product.sku == "ABC-123"
    product.refresh_from_db()
    assert product.sku == "ABC-123"


def test_sku_lookups_ignore_case() -> None:
    product = ProductFactory.create(sku="ABC-123")
    assert Product.objects.get(sku="abc-123") == product


def test_same_sku_twice_for_one_vendor_is_refused_by_the_database() -> None:
    vendor = VendorFactory.create()
    ProductFactory.create(vendor=vendor, sku="ABC-123")
    refused_by_the_database(vendor=vendor, sku="abc-123")


def test_same_sku_for_two_vendors_is_allowed() -> None:
    ProductFactory.create(vendor=VendorFactory.create(), sku="ABC-123")
    ProductFactory.create(vendor=VendorFactory.create(), sku="ABC-123")
    assert Product.objects.filter(sku="ABC-123").count() == 2


def test_validation_reports_a_duplicate_sku_before_saving() -> None:
    vendor = VendorFactory.create()
    ProductFactory.create(vendor=vendor, sku="ABC-123")
    duplicate = ProductFactory.build(vendor=vendor, sku="abc-123")
    with pytest.raises(ValidationError) as exc:
        duplicate.full_clean()
    assert "This vendor already has a product with this SKU." in str(exc.value)


def test_queryset_updates_normalize_the_sku_too() -> None:
    product = ProductFactory.create()
    Product.objects.filter(pk=product.pk).update(sku=" abc-9 ")
    product.refresh_from_db()
    assert product.sku == "ABC-9"


@pytest.mark.parametrize("sku", ["lower-case", "", " PADDED"])
def test_unnormalized_skus_are_refused_by_the_database(sku: str) -> None:
    product = ProductFactory.create()
    with pytest.raises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("UPDATE catalog_product SET sku = %s WHERE id = %s", [sku, product.pk])


# --- Product choices and amounts


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kind", "bundle"),
        ("status", "deleted"),
        ("sales_mode", "auction"),
        ("fiscal_category", "zero"),
        ("stock_policy", "never"),
        ("stock_status", "unknown"),
    ],
)
def test_values_outside_the_choices_are_refused_by_the_database(field: str, value: str) -> None:
    refused_by_the_database(**{field: value})


def test_float_prices_are_refused() -> None:
    with pytest.raises(TypeError):
        ProductFactory.create(price_net=12.5)


def test_negative_price_is_refused_by_the_database() -> None:
    refused_by_the_database(price_net=Decimal("-0.01"))


@pytest.mark.parametrize("sale", [Decimal("10.00"), Decimal("11.00"), Decimal("-1.00")])
def test_sale_price_must_be_below_the_price_and_not_negative(sale: Decimal) -> None:
    refused_by_the_database(price_net=Decimal("10.00"), sale_price_net=sale)


def test_sale_must_end_after_it_starts() -> None:
    refused_by_the_database(
        price_net=Decimal("10.00"), sale_price_net=Decimal("8.00"), sale_from=FEB, sale_to=JAN
    )


def test_sale_period_needs_a_sale_price() -> None:
    refused_by_the_database(sale_from=JAN, sale_to=FEB)


def test_a_valid_sale_is_stored() -> None:
    product = ProductFactory.create(
        price_net=Decimal("10.00"), sale_price_net=Decimal("8.00"), sale_from=JAN, sale_to=FEB
    )
    product.refresh_from_db()
    assert product.sale_price_net == Decimal("8.00")


def test_negative_stock_is_refused_by_the_database() -> None:
    refused_by_the_database(stock_qty=-1)


def test_backorders_need_the_always_open_stock_policy() -> None:
    refused_by_the_database(stock_status="on_backorder", stock_policy="track")
    product = ProductFactory.create(stock_status="on_backorder", stock_policy="always_open")
    assert product.stock_status == Product.StockStatus.ON_BACKORDER


@pytest.mark.parametrize("field", ["weight_kg", "length_cm", "width_cm", "height_cm"])
def test_negative_weight_and_dimensions_are_refused_by_the_database(field: str) -> None:
    refused_by_the_database(**{field: Decimal("-1")})


def test_gtin_must_be_8_to_14_digits() -> None:
    product = ProductFactory.build(gtin="ISBN-123")
    with pytest.raises(ValidationError) as exc:
        product.full_clean()
    assert "gtin" in exc.value.message_dict
    ProductFactory.build(gtin="9788804668237", vendor=VendorFactory.create()).full_clean()


def test_legacy_ids_are_unique() -> None:
    ProductFactory.create(legacy_wp_id_en=101, legacy_wp_id_it=102)
    refused_by_the_database(legacy_wp_id_en=101)
    refused_by_the_database(legacy_wp_id_it=102)


# --- Product relations


def test_required_companion_and_upsells() -> None:
    companion = ProductFactory.create()
    upsell = ProductFactory.create()
    product = ProductFactory.create(required_companion=companion)
    product.upsells.add(upsell)
    assert list(companion.required_by.all()) == [product]
    assert list(product.upsells.all()) == [upsell]
    assert list(upsell.upsold_by.all()) == [product]  # not symmetrical


def test_a_product_cannot_be_its_own_companion() -> None:
    product = ProductFactory.create()
    with pytest.raises(IntegrityError), transaction.atomic():
        Product.objects.filter(pk=product.pk).update(required_companion=product)


def test_a_required_companion_cannot_be_deleted() -> None:
    companion = ProductFactory.create()
    ProductFactory.create(required_companion=companion)
    with pytest.raises(ProtectedError):
        companion.delete()


def test_a_vendor_with_products_cannot_be_deleted() -> None:
    product = ProductFactory.create()
    with pytest.raises(ProtectedError):
        product.vendor.delete()


def test_deleting_a_product_deletes_its_translations_and_variants() -> None:
    product = ProductFactory.create(kind="variable", languages=["en", "it"])
    VariantTranslationFactory.create(variant=VariantFactory.create(product=product))
    product.delete()
    assert ProductTranslation.objects.count() == 0
    assert Variant.objects.count() == 0
    assert VariantTranslation.objects.count() == 0


# --- Variant


def test_new_variant_defaults() -> None:
    product = ProductFactory.create(kind="variable")
    variant = Variant.objects.create(product=product, sku="var-1")
    assert variant.sku == "VAR-1"
    assert variant.vendor == product.vendor
    assert variant.price_net_override is None
    assert variant.weight_kg_override is None
    assert variant.stock_qty == 0
    assert variant.stock_status == Variant.StockStatus.IN_STOCK
    assert variant.is_default is False
    assert variant.position == 0
    assert str(variant) == "VAR-1"


def test_variant_sku_is_unique_per_vendor_across_products() -> None:
    first = ProductFactory.create(kind="variable")
    second = ProductFactory.create(kind="variable", vendor=first.vendor)
    VariantFactory.create(product=first, sku="VAR-RED")
    with pytest.raises(IntegrityError), transaction.atomic():
        VariantFactory.create(product=second, sku="var-red")


def test_same_variant_sku_for_two_vendors_is_allowed() -> None:
    VariantFactory.create(sku="VAR-RED")
    VariantFactory.create(sku="VAR-RED")
    assert Variant.objects.filter(sku="VAR-RED").count() == 2


def test_validation_reports_a_duplicate_variant_sku_before_saving() -> None:
    existing = VariantFactory.create(sku="VAR-RED")
    other = ProductFactory.create(kind="variable", vendor=existing.vendor)
    duplicate = VariantFactory.build(product=other, sku="var-red")
    with pytest.raises(ValidationError) as exc:
        duplicate.full_clean()
    assert "sku" in exc.value.message_dict


def test_the_variant_vendor_always_follows_its_product() -> None:
    variant = VariantFactory.create()
    other_vendor = VendorFactory.create()
    variant.vendor = other_vendor
    variant.save()
    variant.refresh_from_db()
    assert variant.vendor == variant.product.vendor


def test_a_variant_vendor_different_from_its_product_is_refused_by_the_database() -> None:
    variant = VariantFactory.create()
    other_vendor = VendorFactory.create()
    with pytest.raises(IntegrityError), transaction.atomic():
        Variant.objects.filter(pk=variant.pk).update(vendor=other_vendor)


def test_only_one_default_variant_per_product() -> None:
    product = ProductFactory.create(kind="variable")
    VariantFactory.create(product=product, is_default=True)
    VariantFactory.create(product=product, is_default=False)
    with pytest.raises(IntegrityError), transaction.atomic():
        VariantFactory.create(product=product, is_default=True)
    VariantFactory.create(is_default=True)  # another product may have its own default


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("stock_qty", -1),
        ("price_net_override", Decimal("-0.01")),
        ("weight_kg_override", Decimal("-1")),
        ("stock_status", "unknown"),
    ],
)
def test_invalid_variant_values_are_refused_by_the_database(field: str, value: object) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        VariantFactory.create(**{field: value})


def test_unnormalized_variant_skus_are_refused_by_the_database() -> None:
    variant = VariantFactory.create()
    with pytest.raises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("UPDATE catalog_variant SET sku = 'lower' WHERE id = %s", [variant.pk])


def test_variants_are_ordered_by_position() -> None:
    product = ProductFactory.create(kind="variable")
    second = VariantFactory.create(product=product, position=2)
    first = VariantFactory.create(product=product, position=1)
    assert list(product.variants.all()) == [first, second]


# --- Translations


def test_one_translation_per_product_and_language() -> None:
    product = ProductFactory.create(languages=["en"])
    with pytest.raises(IntegrityError), transaction.atomic():
        ProductTranslationFactory.create(product=product, language="en")


def test_slugs_are_unique_per_language_once_set() -> None:
    ProductTranslationFactory.create(language="en", slug="red-bag-en-sku-1")
    ProductTranslationFactory.create(language="it", slug="red-bag-en-sku-1")
    ProductTranslationFactory.create(language="en", slug="")
    ProductTranslationFactory.create(language="en", slug="")  # empty: not generated yet (CAT.5)
    with pytest.raises(IntegrityError), transaction.atomic():
        ProductTranslationFactory.create(language="en", slug="red-bag-en-sku-1")


def test_one_translation_per_variant_and_language() -> None:
    variant = VariantFactory.create()
    VariantTranslationFactory.create(variant=variant, language="en")
    with pytest.raises(IntegrityError), transaction.atomic():
        VariantTranslationFactory.create(variant=variant, language="en")


def test_product_texts_fall_back_to_published_english() -> None:
    product = ProductFactory.create()
    ProductTranslationFactory.create(product=product, language="en", status="published")
    ProductTranslationFactory.create(product=product, language="it", status="machine")
    resolved = product.translation("it")
    assert resolved.language == "en"
    assert resolved.is_fallback is True


def test_changing_the_english_title_sends_translations_back_to_review() -> None:
    product = ProductFactory.create()
    en = ProductTranslationFactory.create(product=product, language="en", status="published")
    it = ProductTranslationFactory.create(product=product, language="it", status="published")
    en.title = "A new title"
    en.save()
    it.refresh_from_db()
    assert it.status == "machine"
    assert it.is_stale is True


def test_variant_texts_fall_back_to_published_english() -> None:
    variant = VariantFactory.create()
    VariantTranslationFactory.create(variant=variant, language="en", status="published")
    assert variant.translation("de").language == "en"
