"""CAT.12: audited product and variant edits (services).

Changes to `fiscal_category`, `sales_mode`, weight and dimensions go through
`catalog.services` with dry-run, a ChangeSet and the AuditLog (before and after). The owning
vendor never changes (owner decision Q-33, CAT.14). Prices keep their own service (CAT.1).
"""

from decimal import Decimal
from typing import Any

import pytest
from django.core.exceptions import ValidationError

from apps.catalog.models import Product, Variant
from apps.catalog.services import (
    delete_product,
    delete_variant,
    save_product,
    save_variant,
)
from apps.catalog.tests.factories import ProductFactory, VariantFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:catalog", source="admin", ip="127.0.0.1")


def test_a_fiscal_category_change_is_applied_and_audited_with_before_and_after() -> None:
    product = ProductFactory.create(sku="SKU-1")
    product.fiscal_category = Product.FiscalCategory.REDUCED
    changes = save_product(product, ctx=CTX)

    assert changes.summary() == {"create": 0, "update": 1, "delete": 0}
    product.refresh_from_db()
    assert product.fiscal_category == Product.FiscalCategory.REDUCED

    entry = AuditLog.objects.get()
    assert entry.action == "catalog.product.updated"
    assert entry.target_model == "catalog.product"
    assert entry.target_key == str(product.public_id)
    assert entry.vendor_key == str(product.vendor.public_id)
    assert entry.actor_label == "staff:catalog"
    before = entry.before or {}
    after = entry.after or {}
    assert before["sku"] == "SKU-1"
    assert before["vendor"] == str(product.vendor.public_id)
    assert before["fiscal_category"] == "standard"
    assert after["fiscal_category"] == "reduced"


@pytest.mark.parametrize(
    ("field", "value", "logged"),
    [
        ("sales_mode", Product.SalesMode.ON_REQUEST, "on_request"),
        ("weight_kg", Decimal("1.250"), "1.250"),
        ("length_cm", Decimal("30.00"), "30.00"),
        ("width_cm", Decimal("20.50"), "20.50"),
        ("height_cm", Decimal("4.00"), "4.00"),
    ],
)
def test_sales_mode_weight_and_dimensions_are_audited(field: str, value: Any, logged: str) -> None:
    product = ProductFactory.create()
    setattr(product, field, value)
    save_product(product, ctx=CTX)
    entry = AuditLog.objects.get()
    assert (entry.after or {})[field] == logged
    assert (entry.before or {})[field] != logged


def test_a_dry_run_returns_the_change_and_writes_nothing() -> None:
    product = ProductFactory.create(weight_kg=Decimal("0.500"))
    product.fiscal_category = Product.FiscalCategory.BOOK_ART74
    product.weight_kg = Decimal("0.750")
    changes = save_product(product, ctx=CTX, dry_run=True)

    assert changes.to_dict()["changes"] == [
        {
            "op": "update",
            "model": "catalog.product",
            "key": str(product.public_id),
            "fields": {
                "fiscal_category": ["standard", "book_art74"],
                "weight_kg": ["0.500", "0.750"],
            },
        }
    ]
    stored = Product.objects.get(pk=product.pk)
    assert stored.fiscal_category == Product.FiscalCategory.STANDARD
    assert stored.weight_kg == Decimal("0.500")
    assert AuditLog.objects.count() == 0


def test_the_change_is_computed_against_the_stored_row_not_a_stale_instance() -> None:
    product = ProductFactory.create()
    stale = Product.objects.get(pk=product.pk)
    product.sales_mode = Product.SalesMode.CATALOG_ONLY
    save_product(product, ctx=CTX)
    stale.sales_mode = Product.SalesMode.ON_REQUEST
    changes = save_product(stale, ctx=CTX)
    assert changes.changes[0].fields["sales_mode"] == ("catalog_only", "on_request")


def test_other_fields_are_saved_without_an_audit_entry() -> None:
    product = ProductFactory.create(stock_qty=1)
    product.stock_qty = 9
    changes = save_product(product, ctx=CTX)
    assert changes.is_empty
    product.refresh_from_db()
    assert product.stock_qty == 9
    assert AuditLog.objects.count() == 0


@pytest.mark.parametrize("dry_run", [False, True])
def test_the_owning_vendor_never_changes(dry_run: bool) -> None:
    product = ProductFactory.create()
    owner = product.vendor
    product.vendor = VendorFactory.create()
    product.fiscal_category = Product.FiscalCategory.REDUCED
    with pytest.raises(ValidationError) as exc:
        save_product(product, ctx=CTX, dry_run=dry_run)
    assert "vendor" in exc.value.message_dict
    stored = Product.objects.get(pk=product.pk)
    assert stored.vendor == owner
    assert stored.fiscal_category == Product.FiscalCategory.STANDARD
    assert AuditLog.objects.count() == 0


def test_prices_are_not_changed_by_the_edit_service() -> None:
    product = ProductFactory.create(price_net=Decimal("10.00"))
    product.price_net = Decimal("12.00")
    with pytest.raises(ValidationError) as exc:
        save_product(product, ctx=CTX)
    assert "price_net" in exc.value.message_dict
    assert Product.objects.get(pk=product.pk).price_net == Decimal("10.00")
    assert AuditLog.objects.count() == 0


def test_an_invalid_value_is_refused_also_on_dry_run() -> None:
    product = ProductFactory.create()
    product.weight_kg = Decimal("-1.000")
    with pytest.raises(ValidationError):
        save_product(product, ctx=CTX, dry_run=True)
    assert AuditLog.objects.count() == 0


def test_creating_a_product_is_audited() -> None:
    vendor = VendorFactory.create()
    product = ProductFactory.build(
        vendor=vendor, sku="NEW-1", fiscal_category=Product.FiscalCategory.REDUCED
    )
    changes = save_product(product, ctx=CTX)
    assert changes.summary()["create"] == 1
    assert Product.objects.filter(pk=product.pk).exists()
    entry = AuditLog.objects.get()
    assert entry.action == "catalog.product.created"
    assert entry.before is None
    assert (entry.after or {})["fiscal_category"] == "reduced"
    assert entry.vendor_key == str(vendor.public_id)


def test_a_creation_dry_run_writes_nothing() -> None:
    product = ProductFactory.build(vendor=VendorFactory.create())
    changes = save_product(product, ctx=CTX, dry_run=True)
    assert changes.summary()["create"] == 1
    assert Product.objects.count() == 0
    assert AuditLog.objects.count() == 0


def test_deleting_a_product_audits_it_and_its_variants() -> None:
    variant = VariantFactory.create()
    product = variant.product
    changes = delete_product(product, ctx=CTX)
    assert changes.summary() == {"create": 0, "update": 0, "delete": 2}
    assert not Product.objects.filter(pk=product.pk).exists()
    assert set(AuditLog.objects.values_list("action", "target_key")) == {
        ("catalog.product.deleted", str(product.public_id)),
        ("catalog.variant.deleted", str(variant.public_id)),
    }


def test_a_delete_dry_run_writes_nothing() -> None:
    product = ProductFactory.create()
    delete_product(product, ctx=CTX, dry_run=True)
    assert Product.objects.filter(pk=product.pk).exists()
    assert AuditLog.objects.count() == 0


def test_a_variant_weight_change_is_audited() -> None:
    variant = VariantFactory.create(sku="VAR-1")
    variant.weight_kg_override = Decimal("2.000")
    save_variant(variant, ctx=CTX)
    variant.refresh_from_db()
    assert variant.weight_kg_override == Decimal("2.000")
    entry = AuditLog.objects.get()
    assert entry.action == "catalog.variant.updated"
    assert entry.target_key == str(variant.public_id)
    assert entry.vendor_key == str(variant.vendor.public_id)
    assert (entry.before or {})["weight_kg_override"] is None
    assert (entry.after or {})["weight_kg_override"] == "2.000"


def test_a_variant_dry_run_writes_nothing() -> None:
    variant = VariantFactory.create()
    variant.weight_kg_override = Decimal("2.000")
    changes = save_variant(variant, ctx=CTX, dry_run=True)
    assert not changes.is_empty
    assert Variant.objects.get(pk=variant.pk).weight_kg_override is None
    assert AuditLog.objects.count() == 0


def test_a_variant_cannot_move_to_another_vendors_product() -> None:
    variant = VariantFactory.create()
    owner = variant.vendor
    variant.product = ProductFactory.create(kind=Product.Kind.VARIABLE)
    with pytest.raises(ValidationError) as exc:
        save_variant(variant, ctx=CTX)
    assert "product" in exc.value.message_dict
    assert Variant.objects.get(pk=variant.pk).vendor == owner
    assert AuditLog.objects.count() == 0


def test_a_variant_price_is_not_changed_by_the_edit_service() -> None:
    variant = VariantFactory.create()
    variant.price_net_override = Decimal("5.00")
    with pytest.raises(ValidationError) as exc:
        save_variant(variant, ctx=CTX)
    assert "price_net_override" in exc.value.message_dict
    assert Variant.objects.get(pk=variant.pk).price_net_override is None


def test_creating_and_deleting_a_variant_is_audited() -> None:
    product = ProductFactory.create(kind=Product.Kind.VARIABLE)
    variant = VariantFactory.build(product=product, sku="VAR-NEW")
    save_variant(variant, ctx=CTX)
    assert variant.vendor == product.vendor
    delete_variant(variant, ctx=CTX)
    assert not Variant.objects.filter(sku="VAR-NEW").exists()
    assert list(AuditLog.objects.order_by("id").values_list("action", flat=True)) == [
        "catalog.variant.created",
        "catalog.variant.deleted",
    ]


def test_a_variant_delete_dry_run_writes_nothing() -> None:
    variant = VariantFactory.create()
    changes = delete_variant(variant, ctx=CTX, dry_run=True)
    assert changes.summary()["delete"] == 1
    assert Variant.objects.filter(pk=variant.pk).exists()
    assert AuditLog.objects.count() == 0
