"""CAT.14: catalogue integrity rules (owner decisions Q-29, Q-31, Q-32, Q-33; spec 03 §3.2).

Each rule holds in the database (bulk updates, imports, raw SQL) and is reported as a
ValidationError by `full_clean` so forms show it before saving.
"""

import datetime as dt
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.tests.factories import UserFactory
from apps.catalog import engine
from apps.catalog.models import Product, ProductTranslation, StockStatus, Variant
from apps.catalog.services import (
    duplicate_product_to_vendor,
    set_product_price,
    suggested_upsells,
)
from apps.catalog.tests.factories import (
    BrandFactory,
    ProductFactory,
    ProductTranslationFactory,
    VariantFactory,
    VariantTranslationFactory,
)
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.vendors.models import Vendor
from apps.vendors.services import set_cross_vendor_consent
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:catalog", source="admin", ip="127.0.0.1")
DUPLICATED = "catalog.product.duplicated_to_vendor"


def opt_in(vendor: Vendor, *, in_suggestions: bool = True, days_ago: int = 1) -> None:
    set_cross_vendor_consent(
        vendor,
        in_suggestions=in_suggestions,
        valid_from=timezone.now() - dt.timedelta(days=days_ago),
        ctx=CTX,
    )


# --- Price (Q-31)


def test_a_product_for_sale_needs_a_price_in_the_database() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        ProductFactory.create(sales_mode="buy", price_net=None)


@pytest.mark.parametrize("mode", ["on_request", "catalog_only"])
def test_a_product_not_for_sale_may_have_no_price(mode: str) -> None:
    product = ProductFactory.create(sales_mode=mode, price_net=None)
    product.refresh_from_db()
    assert product.price_net is None


@pytest.mark.parametrize("mode", ["buy", "on_request"])
def test_a_zero_price_is_refused_by_the_database(mode: str) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        ProductFactory.create(sales_mode=mode, price_net=Decimal("0.00"))


def test_validation_reports_a_missing_price_for_sale() -> None:
    product = ProductFactory.build(sales_mode="buy", price_net=None, vendor=VendorFactory.create())
    with pytest.raises(ValidationError) as exc:
        product.full_clean()
    assert "price_net" in exc.value.message_dict


def test_the_price_service_refuses_zero_and_clears_only_when_not_for_sale() -> None:
    product = ProductFactory.create(sales_mode="buy", price_net=Decimal("10.00"))
    with pytest.raises(ValidationError):
        set_product_price(product, price_net=Decimal("0.00"), ctx=CTX)
    with pytest.raises(ValidationError):
        set_product_price(product, price_net=None, ctx=CTX)
    Product.objects.filter(pk=product.pk).update(sales_mode="on_request")
    set_product_price(product, price_net=None, ctx=CTX)
    product.refresh_from_db()
    assert product.price_net is None


def test_regression_price_on_request() -> None:
    """PRICE-ON-REQUEST (spec 03 §3.3): no public price while on request; the stored price is
    kept for the vendor and staff and restored when the product is for sale again."""
    product = ProductFactory.create(sales_mode="buy", price_net=Decimal("120.00"))
    assert engine.public_price(product) == Decimal("120.00")

    product.sales_mode = Product.SalesMode.ON_REQUEST
    product.full_clean()
    product.save()
    product.refresh_from_db()
    assert engine.public_price(product) is None
    assert product.price_net == Decimal("120.00")  # what the vendor and staff see

    product.sales_mode = Product.SalesMode.BUY
    product.full_clean()
    product.save()
    product.refresh_from_db()
    assert engine.public_price(product) == Decimal("120.00")


# --- Identifiers (Q-33)


def test_a_product_stores_its_manufacturer_part_number() -> None:
    product = ProductFactory.create(mpn="AB-1234/X")
    product.refresh_from_db()
    assert product.mpn == "AB-1234/X"
    assert ProductFactory.create().mpn == ""


# --- Vendor ownership (Q-33)


def test_changing_a_products_vendor_is_refused_by_the_database() -> None:
    product = ProductFactory.create()
    other = VendorFactory.create()
    with pytest.raises(IntegrityError), transaction.atomic():
        Product.objects.filter(pk=product.pk).update(vendor=other)
    product.vendor = other
    with pytest.raises(IntegrityError), transaction.atomic():
        product.save()
    product.refresh_from_db()
    assert product.vendor != other


def test_validation_reports_a_vendor_change() -> None:
    product = ProductFactory.create()
    product.vendor = VendorFactory.create()
    with pytest.raises(ValidationError) as exc:
        product.full_clean()
    assert "vendor" in exc.value.message_dict


# --- Variants only on variable products (Q-29, Q-32)


def test_a_simple_product_cannot_get_a_variant() -> None:
    simple = ProductFactory.create(kind="simple")
    with pytest.raises(IntegrityError), transaction.atomic():
        VariantFactory.create(product=simple)
    with pytest.raises(ValidationError) as exc:
        VariantFactory.build(product=simple).full_clean()
    assert "product" in exc.value.message_dict


def test_a_variable_product_with_variants_cannot_become_simple() -> None:
    variant = VariantFactory.create()
    product = variant.product
    with pytest.raises(IntegrityError), transaction.atomic():
        Product.objects.filter(pk=product.pk).update(kind="simple")
    product.kind = Product.Kind.SIMPLE
    with pytest.raises(ValidationError) as exc:
        product.full_clean()
    assert "kind" in exc.value.message_dict


def test_a_variable_product_without_variants_can_become_simple() -> None:
    product = ProductFactory.create(kind="variable")
    Product.objects.filter(pk=product.pk).update(kind="simple")
    product.refresh_from_db()
    assert product.kind == "simple"


# --- Required companion of the same vendor (Q-32)


def test_a_required_companion_of_the_same_vendor_is_accepted() -> None:
    product = ProductFactory.create()
    companion = ProductFactory.create(vendor=product.vendor)
    product.required_companion = companion
    product.full_clean()
    product.save()


def test_another_vendors_companion_is_refused() -> None:
    product = ProductFactory.create()
    foreign = ProductFactory.create()
    product.required_companion = foreign
    with pytest.raises(ValidationError) as exc:
        product.full_clean()
    assert "required_companion" in exc.value.message_dict
    with pytest.raises(IntegrityError), transaction.atomic():
        Product.objects.filter(pk=product.pk).update(required_companion=foreign)


# --- Suggestions (upsells) across vendors (Q-32)


def test_an_upsell_of_the_same_vendor_needs_no_consent() -> None:
    product = ProductFactory.create()
    same = ProductFactory.create(vendor=product.vendor)
    product.upsells.add(same)
    assert list(product.upsells.all()) == [same]


def test_an_upsell_to_a_vendor_without_opt_in_is_refused() -> None:
    product = ProductFactory.create()
    foreign = ProductFactory.create()
    with pytest.raises(ValidationError), transaction.atomic():
        product.upsells.add(foreign)
    with pytest.raises(ValidationError), transaction.atomic():
        foreign.upsold_by.add(product)
    with pytest.raises(ValidationError), transaction.atomic():
        product.upsells.set([foreign])
    assert not product.upsells.exists()


def test_an_upsell_to_a_vendor_that_opted_out_is_refused() -> None:
    product = ProductFactory.create()
    foreign = ProductFactory.create()
    opt_in(foreign.vendor, in_suggestions=True, days_ago=2)
    opt_in(foreign.vendor, in_suggestions=False, days_ago=1)
    with pytest.raises(ValidationError), transaction.atomic():
        product.upsells.add(foreign)


def test_an_upsell_to_a_vendor_that_opted_in_is_accepted() -> None:
    product = ProductFactory.create()
    foreign = ProductFactory.create()
    opt_in(foreign.vendor)
    product.upsells.add(foreign)
    assert list(product.upsells.all()) == [foreign]


def test_suggestions_hide_vendors_that_withdrew_their_consent() -> None:
    product = ProductFactory.create()
    same = ProductFactory.create(vendor=product.vendor)
    staying = ProductFactory.create()
    leaving = ProductFactory.create()
    opt_in(staying.vendor)
    opt_in(leaving.vendor)
    product.upsells.add(same, staying, leaving)
    set_cross_vendor_consent(leaving.vendor, in_suggestions=False, ctx=CTX)
    assert set(suggested_upsells(product)) == {same, staying}


# --- Staff duplicate to another vendor (Q-33)


def make_rich_product() -> Product:
    product = ProductFactory.create(
        kind="variable",
        price_net=Decimal("40.00"),
        stock_qty=5,
        mpn="MPN-1",
        gtin="12345678",
        brand=BrandFactory.create(),
        legacy_wp_id_en=101,
    )
    ProductTranslationFactory.create(product=product, language="en", slug="bag-en")
    ProductTranslationFactory.create(product=product, language="it")
    variant = VariantFactory.create(
        product=product, sku="VAR-RED", stock_qty=4, stock_status=StockStatus.IN_STOCK
    )
    VariantTranslationFactory.create(variant=variant, language="en", label="Red")
    product.required_companion = ProductFactory.create(vendor=product.vendor)
    product.save()
    product.upsells.add(ProductFactory.create(vendor=product.vendor))
    return product


def test_staff_duplicate_a_product_to_another_vendor_as_a_draft() -> None:
    original = make_rich_product()
    target = VendorFactory.create()
    staff = UserFactory.create(platform_role="catalog")
    original_vendor = original.vendor

    changes = duplicate_product_to_vendor(
        original, target, actor=staff, reason="business transfer", ctx=CTX
    )
    assert changes.summary()["create"] >= 1

    copy = Product.objects.get(vendor=target)
    assert copy.pk != original.pk
    assert copy.public_id != original.public_id
    assert copy.status == Product.Status.DRAFT
    assert (copy.sku, copy.kind, copy.price_net) == (original.sku, "variable", Decimal("40.00"))
    assert (copy.mpn, copy.gtin, copy.brand) == ("MPN-1", "12345678", original.brand)
    assert copy.stock_qty == 0
    assert copy.legacy_wp_id_en is None
    assert copy.required_companion is None
    assert not copy.upsells.exists()
    assert set(copy.translations.values_list("language", flat=True)) == {"en", "it"}
    copied_slugs = ProductTranslation.objects.filter(product=copy).values_list("slug", flat=True)
    assert all(copied_slugs)
    copied_variant = Variant.objects.get(product=copy)
    assert copied_variant.vendor == target
    assert copied_variant.sku == "VAR-RED"
    assert copied_variant.stock_qty == 0
    assert copied_variant.stock_status == StockStatus.OUT_OF_STOCK
    assert list(copied_variant.translations.values_list("label", flat=True)) == ["Red"]

    original.refresh_from_db()
    assert original.vendor == original_vendor
    assert original.legacy_wp_id_en == 101
    assert original.required_companion is not None
    assert original.variants.count() == 1

    entry = AuditLog.objects.get(action=DUPLICATED, target_key=str(copy.public_id))
    assert entry.after is not None
    assert entry.after["duplicated_from"] == str(original.public_id)
    assert entry.after["reason"] == "business transfer"
    assert entry.after["vendor"] == str(target.public_id)


def test_a_dry_run_duplicate_writes_nothing() -> None:
    original = make_rich_product()
    target = VendorFactory.create()
    staff = UserFactory.create(platform_role="catalog")
    count = Product.objects.count()
    changes = duplicate_product_to_vendor(
        original, target, actor=staff, reason="mistake", ctx=CTX, dry_run=True
    )
    assert not changes.is_empty
    assert Product.objects.count() == count
    assert not AuditLog.objects.filter(action=DUPLICATED).exists()


def test_only_staff_can_duplicate_a_product() -> None:
    original = ProductFactory.create()
    with pytest.raises(PermissionDenied):
        duplicate_product_to_vendor(
            original,
            VendorFactory.create(),
            actor=UserFactory.create(),
            reason="transfer",
            ctx=CTX,
        )


def test_a_duplicate_needs_a_reason_and_another_vendor() -> None:
    original = ProductFactory.create()
    staff = UserFactory.create(platform_role="catalog")
    with pytest.raises(ValidationError):
        duplicate_product_to_vendor(
            original, VendorFactory.create(), actor=staff, reason="  ", ctx=CTX
        )
    with pytest.raises(ValidationError):
        duplicate_product_to_vendor(original, original.vendor, actor=staff, reason="x", ctx=CTX)


def test_a_duplicate_onto_a_taken_sku_is_refused() -> None:
    original = ProductFactory.create(sku="SKU-TAKEN")
    target = VendorFactory.create()
    ProductFactory.create(vendor=target, sku="SKU-TAKEN")
    with pytest.raises(ValidationError):
        duplicate_product_to_vendor(
            original, target, actor=UserFactory.create(platform_role="catalog"), reason="x", ctx=CTX
        )
    assert Product.objects.filter(vendor=target).count() == 1


# --- Duplicates and the vendor SKU namespace (CAT.13): fresh identity, match keys


def test_a_duplicate_gets_its_own_staff_refs_and_match_keys() -> None:
    original = make_rich_product()
    original_variant = original.variants.get()
    target = VendorFactory.create()
    duplicate_product_to_vendor(
        original,
        target,
        actor=UserFactory.create(platform_role="catalog"),
        reason="transfer",
        ctx=CTX,
    )
    copy = Product.objects.get(vendor=target)
    copied_variant = Variant.objects.get(product=copy)
    assert copy.staff_ref.startswith("P-")
    assert copy.staff_ref != original.staff_ref
    assert copied_variant.staff_ref.startswith("V-")
    assert copied_variant.staff_ref != original_variant.staff_ref
    assert copy.sku_key == engine.sku_match_key(copy.sku)
    assert copied_variant.sku_key == engine.sku_match_key(copied_variant.sku)


def test_a_duplicate_onto_a_variant_holding_the_product_sku_is_refused() -> None:
    original = ProductFactory.create(sku="Bag 100")
    target = VendorFactory.create()
    VariantFactory.create(product__vendor=target, sku="BAG  100")
    with pytest.raises(ValidationError, match="already has a variant with this SKU"):
        duplicate_product_to_vendor(
            original, target, actor=UserFactory.create(platform_role="catalog"), reason="x", ctx=CTX
        )
    assert Product.objects.filter(vendor=target).count() == 1  # the variant's product


@pytest.mark.parametrize("dry_run", [False, True])
def test_a_duplicate_whose_variant_sku_the_target_uses_is_refused(dry_run: bool) -> None:
    original = ProductFactory.create(kind="variable")
    VariantFactory.create(product=original, sku="VAR RED")
    target = VendorFactory.create()
    ProductFactory.create(vendor=target, sku="var  red")
    VariantFactory.create(product__vendor=target, sku=" Var Red-2")
    VariantFactory.create(product=original, sku="var red-2")
    with pytest.raises(
        ValidationError, match="The vendor already uses these SKUs: VAR RED, var red-2"
    ):
        duplicate_product_to_vendor(
            original,
            target,
            actor=UserFactory.create(platform_role="catalog"),
            reason="x",
            ctx=CTX,
            dry_run=dry_run,
        )
    assert Product.objects.filter(vendor=target).count() == 2


def test_an_admin_login_without_the_permission_cannot_duplicate_a_product() -> None:
    original = ProductFactory.create()
    target = VendorFactory.create()
    for actor in (
        UserFactory.create(staff=True),
        UserFactory.create(platform_staff=True, is_active=False),
    ):
        with pytest.raises(PermissionDenied):
            duplicate_product_to_vendor(original, target, actor=actor, reason="x", ctx=CTX)
    assert not Product.objects.filter(vendor=target).exists()
