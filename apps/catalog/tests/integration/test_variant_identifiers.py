"""CAT.23: variant GTIN, MPN and cover image.

Done when: a GTIN with a wrong check digit is refused; a variant with its own image shows it on
selection (markup here, the browser in tests/e2e/test_variant_image.py); a variant with no GTIN has
none (the product's GTIN is not repeated on it, also in the variant data on the page); a cover
image of another product is refused; a change is audited with before and after; the import and the
API accept and validate the fields (also on a dry run).
"""

import io
import re
from typing import Any

import pytest
from django.apps import apps as django_apps
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import Client
from PIL import Image
from wagtail.models import Site

from apps.catalog import engine, media
from apps.catalog.models import Product, Variant
from apps.catalog.services import create_product, save_variant, variant_snapshot
from apps.catalog.tests.factories import (
    AttributeFactory,
    ProductFactory,
    ProductImageFactory,
    ProductTranslationFactory,
    VariantFactory,
    VariantTranslationFactory,
)
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.core.tests.factories import ImageLicenceFactory
from apps.themes.services import use_theme
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
    AttributeFactory.create(code="size", kind="choice")
    return engine.ProductIn(
        sku="IMP-1",
        kind="variable",
        price_net=engine.to_price("10"),
        translations=(engine.TranslationIn("en", "Imported", "", ""),),
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


# --- Product page


@pytest.fixture(autouse=True)
def _site_allows_indexing() -> None:
    django_apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )


def _png(color: str) -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (640, 480), color).save(buffer, format="PNG")
    return SimpleUploadedFile(f"{color}.png", buffer.getvalue(), content_type="image/png")


def _page() -> tuple[Product, Variant, Variant]:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    product = ProductFactory.create(
        status="published", vendor=vendor, kind="variable", gtin=PRODUCT_GTIN, mpn="PROD-1"
    )
    ProductTranslationFactory.create(
        product=product, language="en", title="Mug", slug="mug", status="published"
    )
    licence = ImageLicenceFactory.create(code="own-work", name="Own work", source_required=False)
    row = media.upload_image(
        product,
        file=_png("green"),
        title="green",
        licence=licence,
        author="The vendor",
        alt_text="A green mug",
        caption="",
        ctx=CTX,
    )
    with_image = VariantFactory.create(
        product=product, sku="MUG-G", position=1, gtin=GOOD, mpn="V-G", cover_image=row
    )
    plain = VariantFactory.create(product=product, sku="MUG-P", position=2, is_default=True)
    for variant, label in ((with_image, "Green"), (plain, "Plain")):
        VariantTranslationFactory.create(
            variant=variant, language="en", label=label, status="published"
        )
    return product, with_image, plain


@pytest.mark.parametrize("theme", ["base", "ride"])
def test_a_variant_with_its_own_image_shows_it_and_one_without_has_none(
    client: Client, theme: str
) -> None:
    _page()
    with use_theme(theme):
        html = client.get("/product/mug/").content.decode()
    rows = re.findall(r"<input[^>]*name=\"variant\"[^>]*>", html)
    own = next(r for r in rows if 'value="MUG-G"' in r)
    plain = next(r for r in rows if 'value="MUG-P"' in r)
    assert "data-variant-image" in own
    assert "data-variant-image" not in plain
    assert f'data-gtin="{GOOD}"' in own
    assert 'data-mpn="V-G"' in own
    # A variant with no GTIN has none: the product's GTIN is not repeated on it.
    assert "data-gtin" not in plain
    assert "data-mpn" not in plain
    assert html.count(PRODUCT_GTIN) == 1  # the specification table only
    # The image is in the page for the selection script and in the variant list (no JS).
    assert html.count("<picture") >= 2
    assert "data-variant-image-panel" in html
    assert 'alt="A green mug"' in html


def test_the_page_builds_no_rendition_for_a_variant_image(client: Client) -> None:
    _page()
    from wagtail.images.models import Rendition

    client.get("/product/mug/")
    assert not Rendition.objects.exists()


def test_a_product_without_variant_images_has_no_panel(client: Client) -> None:
    product = ProductFactory.create(
        status="published",
        vendor=VendorFactory.create(status=Vendor.Status.ACTIVE),
        kind="variable",
    )
    ProductTranslationFactory.create(
        product=product, language="en", title="Cup", slug="cup", status="published"
    )
    VariantFactory.create(product=product, is_default=True)
    ProductImageFactory.create(product=product)
    html = client.get("/product/cup/").content.decode()
    assert "data-variant-image" not in html
