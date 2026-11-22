"""CAT.23: the product page shows a variant's own identifiers and cover image, in both themes.

A variant with its own image carries it in the variant list and as the lead of its gallery
(CAT.26); a variant with no GTIN has none (the product's GTIN is not repeated on it); the
page builds no rendition. The browser behaviour is in tests/e2e/test_variant_image.py.
"""

import io
import re

import pytest
from django.apps import apps as django_apps
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from PIL import Image
from wagtail.models import Site

from apps.catalog import media, slugs
from apps.catalog.models import Product, Variant
from apps.catalog.tests.factories import (
    ProductFactory,
    ProductImageFactory,
    ProductTranslationFactory,
    VariantFactory,
    VariantTranslationFactory,
)
from apps.core.audit import AuditContext
from apps.core.i18n import primary_language
from apps.core.tests.factories import ImageLicenceFactory
from apps.themes.services import use_theme
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

# The variant choices are in the buy form, shown with a consumer price (TAX.12, #757).
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("consumer_prices")]

GOOD = "4006381333931"
PRODUCT_GTIN = "96385074"

# --- Product page


MUG = slugs.product_path(primary_language(), "mug", default_language=settings.LANGUAGE_CODE)
CUP = slugs.product_path(primary_language(), "cup", default_language=settings.LANGUAGE_CODE)


@pytest.fixture(autouse=True)
def _site_allows_indexing() -> None:
    django_apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )


def _png(color: str) -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (640, 480), color).save(buffer, format="PNG")
    return SimpleUploadedFile(f"{color}.png", buffer.getvalue(), content_type="image/png")


MEDIA_CTX = AuditContext(actor_label="system:test", source="system")


def _page() -> tuple[Product, Variant, Variant]:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE, sells_online=True)
    product = ProductFactory.create(
        status="published", vendor=vendor, kind="variable", gtin=PRODUCT_GTIN, mpn="PROD-1"
    )
    ProductTranslationFactory.create(
        product=product, language=primary_language(), title="Mug", slug="mug", status="published"
    )
    licence = ImageLicenceFactory.create(code="own-work", name="Own work", source_required=False)
    media.upload_image(
        product,
        file=_png("green"),
        title="green",
        licence=licence,
        author="The vendor",
        alt_text="A green mug",
        caption="",
        ctx=MEDIA_CTX,
    )
    row = product.images.get()
    with_image = VariantFactory.create(
        product=product, sku="MUG-G", position=1, gtin=GOOD, mpn="V-G", cover_image=row
    )
    plain = VariantFactory.create(product=product, sku="MUG-P", position=2, is_default=True)
    for variant, label in ((with_image, "Green"), (plain, "Plain")):
        VariantTranslationFactory.create(
            variant=variant, language=primary_language(), label=label, status="published"
        )
    return product, with_image, plain


@pytest.mark.parametrize("theme", ["base", "ride"])
def test_a_variant_with_its_own_image_shows_it_and_one_without_has_none(
    client: Client, theme: str
) -> None:
    _page()
    with use_theme(theme):
        html = client.get(MUG).content.decode()
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
    picker = re.search(r"<fieldset class=\"product-variants.*?</fieldset>", html, re.S)
    assert picker is not None
    assert PRODUCT_GTIN not in picker.group(0)
    assert "PROD-1" not in picker.group(0)
    assert GOOD in picker.group(0)  # the variant's own barcode is listed
    # The cover is in the variant list (no JS) and leads the variant's gallery (CAT.26).
    assert html.count("<picture") >= 2
    assert 'data-variant-gallery-for="MUG-G"' in html
    assert 'alt="A green mug"' in html


def test_the_page_builds_no_rendition_for_a_variant_image(client: Client) -> None:
    _page()
    from wagtail.images.models import Rendition

    client.get(MUG)
    assert not Rendition.objects.exists()


def test_a_product_without_variant_images_has_no_panel(client: Client) -> None:
    product = ProductFactory.create(
        status="published",
        vendor=VendorFactory.create(status=Vendor.Status.ACTIVE, sells_online=True),
        kind="variable",
    )
    ProductTranslationFactory.create(
        product=product,
        language=primary_language(),
        title="Cup",
        slug="cup",
        status="published",
    )
    VariantFactory.create(product=product, is_default=True)
    ProductImageFactory.create(product=product)
    html = client.get(CUP).content.decode()
    assert "data-variant-image" not in html
