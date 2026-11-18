"""CAT.23: variant GTIN, MPN and cover image.

Done when: a GTIN with a wrong check digit is refused; a cover image of another product is
refused; a change is audited with before and after; the import (`create_product`) accepts and
validates the fields, also on a dry run; a variant with no GTIN has none (the product's GTIN is not
copied). The page markup is tested in tests/integration/test_variant_image_page.py, the selection
in the browser in tests/e2e/test_variant_image.py.
"""

from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.catalog import engine
from apps.catalog.models import Variant
from apps.catalog.services import create_product, save_variant, variant_snapshot
from apps.catalog.tests.factories import (
    AttributeFactory,
    AttributeValueFactory,
    ProductFactory,
    ProductImageFactory,
    VariantFactory,
)
from apps.core.audit import AuditContext
from apps.core.i18n import primary_language
from apps.core.models import AuditLog
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:catalog", source="admin", ip="127.0.0.1")
GOOD = "4006381333931"
BAD = "4006381333932"
PRODUCT_GTIN = "96385074"


def test_a_gtin_with_a_wrong_check_digit_is_refused() -> None:
    variant = VariantFactory.build(product=ProductFactory.create(kind="variable"), gtin=BAD)
    with pytest.raises(ValidationError) as exc:
        variant.full_clean()
    assert "gtin" in exc.value.message_dict
    with pytest.raises(ValidationError) as saved:
        save_variant(variant, ctx=CTX)
    assert "gtin" in saved.value.message_dict
    assert not Variant.objects.exists()


@pytest.mark.parametrize("gtin", ["96385074", "036000291452", GOOD, "10614141000415"])
def test_a_valid_gtin_is_accepted_and_empty_is_allowed(gtin: str) -> None:
    product = ProductFactory.create(kind="variable")
    VariantFactory.build(product=product, gtin=gtin).full_clean()
    VariantFactory.build(product=product, gtin="").full_clean()


def test_the_database_refuses_a_malformed_gtin() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        VariantFactory.create(gtin="123")


def test_identifiers_are_normalised_and_the_product_values_are_not_copied() -> None:
    product = ProductFactory.create(kind="variable", gtin=PRODUCT_GTIN, mpn="PROD-1")
    variant = VariantFactory.build(product=product, gtin=f" {GOOD} ", mpn="  V-1 ")
    save_variant(variant, ctx=CTX)
    variant.refresh_from_db()
    assert (variant.gtin, variant.mpn) == (GOOD, "V-1")
    bare = VariantFactory.create(product=product)
    assert (bare.gtin, bare.mpn) == ("", "")
    assert bare.cover_image is None


def test_a_cover_image_of_another_product_is_refused() -> None:
    product = ProductFactory.create(kind="variable")
    foreign = ProductImageFactory.create()
    variant = VariantFactory.build(product=product, cover_image=foreign)
    with pytest.raises(ValidationError) as exc:
        variant.full_clean()
    assert "cover_image" in exc.value.message_dict
    with pytest.raises(ValidationError):
        save_variant(variant, ctx=CTX)
    own = ProductImageFactory.create(product=product)
    VariantFactory.build(product=product, cover_image=own).full_clean()


def test_a_change_is_audited_with_before_and_after() -> None:
    product = ProductFactory.create(kind="variable")
    variant = VariantFactory.create(product=product)
    image = ProductImageFactory.create(product=product)
    variant.gtin, variant.mpn, variant.cover_image = GOOD, "V-9", image
    changes = save_variant(variant, ctx=CTX)
    assert changes.summary() == {"create": 0, "update": 1, "delete": 0}
    entry = AuditLog.objects.get(action="catalog.variant.updated")
    assert entry.target_key == str(variant.public_id)
    before, after = entry.before or {}, entry.after or {}
    assert (before["gtin"], before["mpn"], before["cover_image"]) == ("", "", None)
    assert (after["gtin"], after["mpn"], after["cover_image"]) == (
        GOOD,
        "V-9",
        str(image.public_id),
    )


def test_a_dry_run_validates_and_writes_nothing() -> None:
    product = ProductFactory.create(kind="variable")
    variant = VariantFactory.create(product=product)
    variant.gtin = BAD
    with pytest.raises(ValidationError):
        save_variant(variant, ctx=CTX, dry_run=True)
    variant.gtin = GOOD
    save_variant(variant, ctx=CTX, dry_run=True)
    assert Variant.objects.get(pk=variant.pk).gtin == ""
    assert not AuditLog.objects.filter(action="catalog.variant.updated").exists()


def test_the_snapshot_carries_the_identifiers() -> None:
    snapshot = variant_snapshot(VariantFactory.create(gtin=GOOD, mpn="V-2"))
    assert snapshot["gtin"] == GOOD
    assert snapshot["mpn"] == "V-2"
    assert snapshot["cover_image"] is None


# --- Import (create_product) and the engine's input data


def _size_product(vendor: Vendor, **variant_fields: Any) -> engine.ProductIn:
    attribute = AttributeFactory.create(code="size", kind="choice")
    for position, code in enumerate(("s", "m")):
        AttributeValueFactory.create(attribute=attribute, value=code, position=position)
    return engine.ProductIn(
        sku="IMP-1",
        kind="variable",
        price_net=engine.to_price("10"),
        translations=(engine.TranslationIn(primary_language(), "Imported", "", ""),),
        attributes=(engine.AttributeIn("size", ("s", "m"), None, "", True),),
        variants=(
            engine.VariantIn(sku="IMP-1-S", options={"size": "s"}, **variant_fields),
            engine.VariantIn(sku="IMP-1-M", options={"size": "m"}),
        ),
    )


def test_the_import_refuses_a_wrong_check_digit_also_on_a_dry_run() -> None:
    vendor = VendorFactory.create()
    data = _size_product(vendor, gtin=BAD)
    for dry_run in (True, False):
        with pytest.raises(ValidationError) as exc:
            create_product(vendor, data, ctx=CTX, dry_run=dry_run)
        assert "variants" in exc.value.message_dict


def test_the_import_stores_the_identifiers_of_the_variant_that_has_them() -> None:
    vendor = VendorFactory.create()
    data = _size_product(vendor, gtin=f" {GOOD}", mpn="V-IMP")
    create_product(vendor, data, ctx=CTX)
    own = Variant.objects.get(sku="IMP-1-S")
    other = Variant.objects.get(sku="IMP-1-M")
    assert (own.gtin, own.mpn) == (GOOD, "V-IMP")
    assert (other.gtin, other.mpn) == ("", "")
    created = AuditLog.objects.filter(target_model="catalog.variant", action__endswith="created")
    assert {(e.after or {}).get("gtin") for e in created} == {GOOD, ""}
