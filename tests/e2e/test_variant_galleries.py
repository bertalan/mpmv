"""E2E (CAT.26, CAT.23): choosing a version swaps the gallery for the version's own.

Done when: selecting a variant with its own images shows them first (then the product gallery);
a variant without images shows the product gallery; the choice works from the keyboard; the page
passes axe (WCAG 2.2 AA) and raises no CSP violation, in both themes. Without JavaScript every
version's gallery follows the product gallery under a heading (tests/integration).
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
from apps.catalog.models import Product, Variant
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
    own = VariantFactory.create(product=product, sku="MUG-G", position=1)
    plain = VariantFactory.create(product=product, sku="MUG-P", position=2, is_default=True)
    for variant, label in ((own, "Green"), (plain, "Plain")):
        VariantTranslationFactory.create(
            variant=variant, language=primary_language(), label=label, status="published"
        )
    owners: list[tuple[str, Variant | None]] = [
        ("red", None),
        ("blue", None),
        ("green", own),
        ("lime", own),
    ]
    for color, owner in owners:
        media.upload_image(
            product,
            file=_png(color),
            title=color,
            licence=licence,
            author="The vendor",
            alt_text=f"A {color} mug",
            caption="",
            variant=owner,
            ctx=CTX,
        )
    return product


@pytest.mark.parametrize("theme", ["base", "ride"])
def test_choosing_a_variant_swaps_in_its_gallery_and_back(
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
    product_gallery = page.locator(".gallery").first
    variant_gallery = page.locator("[data-variant-gallery-for='MUG-G']")
    # The default variant has no images of its own: the product gallery shows.
    expect(product_gallery).to_be_visible()
    expect(variant_gallery).to_be_hidden()
    expect(page.locator("[data-variant-gallery-title]").first).to_be_hidden()
    shot(f"{theme}-1-product-gallery")

    page.locator("input[value=MUG-G]").check()
    expect(variant_gallery).to_be_visible()
    expect(product_gallery).to_be_hidden()
    # Its own images come first, then the product's.
    alts = variant_gallery.locator("picture img").evaluate_all("els => els.map(e => e.alt)")
    assert alts.index("A green mug") < alts.index("A red mug")
    assert alts.index("A lime mug") < alts.index("A red mug")
    assert alts[0] in ("A green mug", "A lime mug")
    expect(variant_gallery.get_by_role("img", name=alts[0]).first).to_be_visible()
    shot(f"{theme}-2-variant-gallery")
    check_page(page)

    page.locator("input[value=MUG-P]").check()
    expect(variant_gallery).to_be_hidden()
    expect(product_gallery).to_be_visible()
    check_page(page)


@pytest.mark.parametrize("theme", ["base", "ride"])
def test_the_variant_gallery_follows_the_keyboard(
    page: Page, live_server: LiveServer, settings: Any, mug: Product, theme: str
) -> None:
    settings.MPMV_THEME_DEFAULT = theme
    page.goto(f"{live_server.url}/product/mug/")
    variant_gallery = page.locator("[data-variant-gallery-for='MUG-G']")
    page.locator("input[value=MUG-P]").focus()
    page.keyboard.press("ArrowUp")  # the radio group moves to the previous version
    expect(page.locator("input[value=MUG-G]")).to_be_checked()
    expect(variant_gallery).to_be_visible()
    page.keyboard.press("ArrowDown")
    expect(page.locator("input[value=MUG-P]")).to_be_checked()
    expect(variant_gallery).to_be_hidden()
