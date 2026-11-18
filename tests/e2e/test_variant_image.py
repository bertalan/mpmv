"""E2E (CAT.23): choosing a version with its own picture shows it in place of the gallery.

Done when: selecting a variant that has a cover image swaps the gallery image for it; selecting
one without brings the gallery back; the page passes axe (WCAG 2.2 AA) and raises no CSP
violation, in both themes. Without JavaScript the picture shows in the variant list (the
integration test checks that markup).
"""

import io
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer
from wagtail.models import Collection

from apps.catalog import media
from apps.catalog.models import Product
from apps.catalog.tests.factories import (
    ProductFactory,
    ProductTranslationFactory,
    VariantFactory,
    VariantTranslationFactory,
)
from apps.core.audit import AuditContext
from apps.core.i18n import primary_language
from apps.core.tests.factories import ImageLicenceFactory
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory
from tests.e2e.csp import RECORD_CSP_VIOLATIONS
from tests.e2e.test_checkout import check_page

pytestmark = pytest.mark.django_db(transaction=True)
CTX = AuditContext(actor_label="system:test", source="system")


def _png(color: str) -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (640, 480), color).save(buffer, format="PNG")
    return SimpleUploadedFile(f"{color}.png", buffer.getvalue(), content_type="image/png")


@pytest.fixture
def mug() -> Product:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE, sells_online=True)
    product = ProductFactory.create(status="published", vendor=vendor, kind="variable")
    ProductTranslationFactory.create(
        product=product, language=primary_language(), title="Mug", slug="mug", status="published"
    )
    if Collection.get_first_root_node() is None:  # a flushed (transactional) database has none
        Collection.add_root(name="Root")
    licence = ImageLicenceFactory.create(code="own-work", name="Own work", source_required=False)
    for color in ("red", "green"):
        media.upload_image(
            product,
            file=_png(color),
            title=color,
            licence=licence,
            author="The vendor",
            alt_text=f"A {color} mug",
            caption="",
            ctx=CTX,
        )
    green = product.images.get(image__title="green")
    own = VariantFactory.create(product=product, sku="MUG-G", position=1, cover_image=green)
    plain = VariantFactory.create(product=product, sku="MUG-P", position=2, is_default=True)
    for variant, label in ((own, "Green"), (plain, "Plain")):
        VariantTranslationFactory.create(
            variant=variant, language=primary_language(), label=label, status="published"
        )
    return product


@pytest.mark.parametrize("theme", ["base", "ride"])
def test_choosing_a_variant_with_its_own_image_swaps_the_gallery_image(
    page: Page,
    live_server: LiveServer,
    settings: Any,
    mug: Product,
    shot: Callable[[str], Path],
    theme: str,
) -> None:
    settings.MPMV_THEME_DEFAULT = theme
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    page.goto(f"{live_server.url}/product/mug/")
    gallery = page.locator(".gallery")
    shown = page.locator("[data-variant-image-for='MUG-G']")
    expect(gallery).to_be_visible()
    expect(shown).to_be_hidden()
    shot(f"{theme}-1-gallery")

    page.locator("input[value=MUG-G]").check()
    expect(shown).to_be_visible()
    expect(shown.get_by_role("img", name="A green mug")).to_be_visible()
    expect(gallery).to_be_hidden()
    shot(f"{theme}-2-variant")
    check_page(page)

    page.locator("input[value=MUG-P]").check()
    expect(shown).to_be_hidden()
    expect(gallery).to_be_visible()
    check_page(page)
