"""CAT.26: the product page carries a gallery per variant, in both themes.

Done when: a variant with its own images shows them first (then the product gallery); a variant
without images keeps the product gallery; a dedicated-language variant image shows in that
language only. The browser swap is tests/e2e/test_variant_galleries.py; here the markup the
script uses, which is also the no-JavaScript view (each gallery under a labelled heading).
"""

import io
import re

import pytest
from django.apps import apps as django_apps
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from PIL import Image
from wagtail.images.models import Rendition
from wagtail.models import Site

from apps.catalog import media, slugs
from apps.catalog.models import Product, Variant
from apps.catalog.tests.factories import (
    ProductFactory,
    ProductTranslationFactory,
    VariantFactory,
    VariantTranslationFactory,
)
from apps.core.audit import AuditContext
from apps.core.i18n import primary_language
from apps.core.models import ImageLicence
from apps.core.tests.factories import ImageLicenceFactory
from apps.themes.services import use_theme
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory
from tests.i18n import other_language, product_url

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="system:test", source="system")
MUG = slugs.product_path(primary_language(), "mug", default_language=settings.LANGUAGE_CODE)


@pytest.fixture(autouse=True)
def _site_allows_indexing() -> None:
    django_apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )


def _png(name: str) -> SimpleUploadedFile:
    """A distinct picture per name (the same bytes are one file per vendor, CAT.4)."""
    buffer = io.BytesIO()
    seed = sum(name.encode()) + len(name) * 7
    color = (seed % 256, (seed * 3) % 256, (seed * 7) % 256)
    image = Image.new("RGB", (640, 480), color)
    image.putpixel((seed % 600, seed % 400), (255, 255, 255))
    image.save(buffer, format="PNG")
    return SimpleUploadedFile(f"{name}.png", buffer.getvalue(), content_type="image/png")


def _upload(
    product: Product, color: str, *, variant: Variant | None = None, language: str | None = None
) -> None:
    licence = ImageLicence.objects.filter(code="own-work").first()
    if licence is None:
        licence = ImageLicenceFactory.create(
            code="own-work", name="Own work", source_required=False
        )
    media.upload_image(
        product,
        file=_png(color),
        title=f"{color}-{language or 'any'}",
        licence=licence,
        author="The vendor",
        alt_text=f"A {color} mug",
        caption="",
        variant=variant,
        language=language,
        ctx=CTX,
    )


def _product() -> tuple[Product, Variant, Variant]:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE, sells_online=True)
    product = ProductFactory.create(status="published", vendor=vendor, kind="variable")
    for language in {primary_language(), other_language()}:
        ProductTranslationFactory.create(
            product=product,
            language=language,
            title="Mug",
            slug="mug",
            status="published",
        )
    green = VariantFactory.create(product=product, sku="MUG-G", position=1)
    plain = VariantFactory.create(product=product, sku="MUG-P", position=2, is_default=True)
    for variant, label in ((green, "Green"), (plain, "Plain")):
        VariantTranslationFactory.create(
            variant=variant, language=primary_language(), label=label, status="published"
        )
    _upload(product, "product-shot")
    return product, green, plain


def _section(html: str, sku: str) -> str | None:
    found = re.search(
        rf'<section[^>]*data-variant-gallery-for="{sku}".*?</section>\s*(?=<section|</div>)',
        html,
        re.S,
    )
    return found.group(0) if found else None


@pytest.mark.parametrize("theme", ["base", "ride"])
def test_a_variant_with_images_gets_a_gallery_with_its_images_first(
    client: Client, theme: str
) -> None:
    product, green, _plain = _product()
    _upload(product, "green-front", variant=green)
    _upload(product, "green-back", variant=green)
    with use_theme(theme):
        html = client.get(MUG).content.decode()
    section = _section(html, "MUG-G")
    assert section is not None
    assert section.index("A green-front mug") < section.index("A green-back mug")
    assert section.index("A green-back mug") < section.index("A product-shot mug")
    assert "Green" in section  # a labelled heading: the no-JavaScript view
    radio = next(r for r in re.findall(r"<input[^>]*name=\"variant\"[^>]*>", html) if "MUG-G" in r)
    assert "data-variant-gallery" in radio


@pytest.mark.parametrize("theme", ["base", "ride"])
def test_a_variant_without_images_has_no_gallery_and_keeps_the_product_one(
    client: Client, theme: str
) -> None:
    product, green, _plain = _product()
    _upload(product, "green-front", variant=green)
    with use_theme(theme):
        html = client.get(MUG).content.decode()
    assert _section(html, "MUG-P") is None
    radio = next(r for r in re.findall(r"<input[^>]*name=\"variant\"[^>]*>", html) if "MUG-P" in r)
    assert "data-variant-gallery" not in radio
    assert html.count("A product-shot mug") >= 1  # the product gallery is in the page


def test_a_product_without_variant_images_has_no_variant_galleries(client: Client) -> None:
    _product()
    html = client.get(MUG).content.decode()
    assert "data-variant-gallery" not in html
    assert "data-variant-galleries" not in html


@pytest.mark.parametrize("theme", ["base", "ride"])
def test_the_variant_cover_leads_its_gallery(client: Client, theme: str) -> None:
    product, green, _plain = _product()
    _upload(product, "green-front", variant=green)
    _upload(product, "green-back", variant=green)
    back = product.images.get(variant=green, image__title__startswith="green-back")
    green.cover_image = back
    green.save()
    with use_theme(theme):
        html = client.get(MUG).content.decode()
    section = _section(html, "MUG-G")
    assert section is not None
    assert section.index("A green-back mug") < section.index("A green-front mug")


@pytest.mark.parametrize("theme", ["base", "ride"])
def test_a_variant_image_dedicated_to_a_language_shows_in_that_language_only(
    client: Client, theme: str
) -> None:
    product, green, _plain = _product()
    other = other_language()
    _upload(product, "green-neutral", variant=green)
    _upload(product, "green-dedicated", variant=green, language=other)
    with use_theme(theme):
        primary = client.get(MUG).content.decode()
        dedicated = client.get(product_url(other, "mug")).content.decode()
    assert "A green-neutral mug" in (_section(primary, "MUG-G") or "")
    assert "green-dedicated" not in primary
    section = _section(dedicated, "MUG-G") or ""
    assert "A green-dedicated mug" in section
    assert "A green-neutral mug" not in section


def test_a_variant_image_dedicated_to_another_language_alone_leaves_the_product_gallery(
    client: Client,
) -> None:
    product, green, _plain = _product()
    _upload(product, "green-dedicated", variant=green, language=other_language())
    html = client.get(MUG).content.decode()
    assert _section(html, "MUG-G") is None
    assert "data-variant-gallery" not in html


def test_the_page_builds_no_rendition_for_variant_images(client: Client) -> None:
    product, green, _plain = _product()
    _upload(product, "green-front", variant=green)
    Rendition.objects.all().delete()
    client.get(MUG)
    assert not Rendition.objects.exists()


def test_the_query_count_does_not_grow_with_variants_and_images(client: Client) -> None:
    product, green, _plain = _product()
    _upload(product, "green-front", variant=green)
    client.get(MUG)  # warm caches
    with CaptureQueriesContext(connection) as small:
        client.get(MUG)
    for index in range(4):
        extra = VariantFactory.create(product=product, sku=f"MUG-X{index}", position=10 + index)
        VariantTranslationFactory.create(
            variant=extra, language=primary_language(), label=f"Extra {index}", status="published"
        )
        _upload(product, f"extra-{index}-a", variant=extra)
        _upload(product, f"extra-{index}-b", variant=extra)
    client.get(MUG)
    with CaptureQueriesContext(connection) as large:
        client.get(MUG)
    assert len(large) <= len(small)
