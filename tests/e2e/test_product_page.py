"""E2E (CAT.7): a product page in a real browser, with its screenshot.

Done when: the page shows one h1, the SKU, the vendor link, the default variant selected and the
net price, carries a Product JSON-LD node, and triggers no CSP violation.
"""

import io
import json
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from playwright.sync_api import Page, expect
from pytest_django.fixtures import Settings as SettingsWrapper
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
from apps.core.models import TranslationStatus
from apps.core.tests.factories import ImageLicenceFactory
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.test_cms_pages import RECORD_CSP_VIOLATIONS, csp_violations
from tests.i18n import other_language, product_url

pytestmark = pytest.mark.django_db(transaction=True)

PUBLISHED = TranslationStatus.PUBLISHED


@pytest.fixture
def product_page(consumer_prices: None) -> None:
    """The variant choices are in the buy form, shown only with a consumer price (ORD.19)."""
    vendor = VendorFactory.create(
        status=Vendor.Status.ACTIVE, sells_online=True, display_name="Rossi Ceramics"
    )
    companion = ProductFactory.create(vendor=vendor, status=Product.Status.PUBLISHED)
    ProductTranslationFactory.create(
        product=companion, title="Glaze kit", slug="glaze-kit", status=PUBLISHED
    )
    product = ProductFactory.create(
        vendor=vendor,
        status=Product.Status.PUBLISHED,
        kind=Product.Kind.VARIABLE,
        price_net=Decimal("18.00"),
        required_companion=companion,
    )
    for language, title, slug, text in (
        (primary_language(), "Hand-made mug", "hand-made-mug", "Thrown and glazed by hand."),
        (
            other_language(),
            "Tazza fatta a mano",
            "tazza-fatta-a-mano",
            "Tornita e smaltata a mano.",
        ),
    ):
        ProductTranslationFactory.create(
            product=product,
            language=language,
            title=title,
            slug=slug,
            description=f"<p>{text}</p>",
            status=PUBLISHED,
        )
    for sku, label, default in (("MUG-S", "Small", False), ("MUG-L", "Large", True)):
        variant = VariantFactory.create(product=product, sku=sku, is_default=default)
        VariantTranslationFactory.create(
            variant=variant, language=primary_language(), label=label, status=PUBLISHED
        )


@pytest.mark.usefixtures("product_page")
@pytest.mark.parametrize(
    ("second", "slug", "title"),
    [(False, "hand-made-mug", "Hand-made mug"), (True, "tazza-fatta-a-mano", "Tazza fatta a mano")],
)
def test_a_product_page_shows_its_content_and_structured_data(
    page: Page,
    live_server: LiveServer,
    shot: Callable[[str], Path],
    second: bool,
    slug: str,
    title: str,
) -> None:
    language = other_language() if second else primary_language()
    path = product_url(language, slug)
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    response = page.goto(f"{live_server.url}{path}")
    assert response is not None
    assert response.status == 200
    expect(page.locator("html")).to_have_attribute("lang", language)
    expect(page.get_by_role("heading", level=1)).to_have_text(title)
    expect(page.locator('input[name="variant"]:checked')).to_have_value("MUG-L")
    expect(page.locator(".product-vendor a")).to_have_text("Rossi Ceramics")
    expect(page.locator(".product-companion")).to_contain_text("Glaze kit")
    shot(language)
    graph = json.loads(page.locator('script[type="application/ld+json"]').inner_text())["@graph"]
    assert (
        next(node for node in graph if node["@type"] in {"Product", "ProductGroup"})["name"]
        == title
    )
    assert csp_violations(page) == [], "\n".join(csp_violations(page))


@pytest.mark.parametrize("theme", ["base", "ride"])
@pytest.mark.parametrize("width", [360, 768, 1280])
@pytest.mark.usefixtures("product_page")
def test_the_gallery_and_related_products_pass_axe_and_csp(
    page: Page,
    live_server: LiveServer,
    settings: SettingsWrapper,
    shot: Callable[[str], Path],
    theme: str,
    width: int,
) -> None:
    settings.MPMV_THEME_DEFAULT = theme
    product = Product.objects.get(translations__slug="hand-made-mug")
    if Collection.get_first_root_node() is None:  # a transactional test starts without one
        Collection.add_root(name="Root")
    licence = ImageLicenceFactory.create(code="own-work", name="Own work", source_required=False)
    for color in ("teal", "orange"):
        buffer = io.BytesIO()
        Image.new("RGB", (640, 480), color).save(buffer, format="PNG")
        media.upload_image(
            product,
            file=SimpleUploadedFile(f"{color}.png", buffer.getvalue(), content_type="image/png"),
            title=color,
            licence=licence,
            author="The vendor",
            alt_text=f"A {color} mug",
            ctx=AuditContext(actor_label="system:test", source="system"),
        )
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{live_server.url}{product_url(primary_language(), 'hand-made-mug')}")
    expect(page.locator(".gallery").first).to_be_visible()
    expect(page.locator(".gallery picture").first).to_be_attached()
    shot(f"gallery-{theme}-{width}")
    violations = run_axe(page)
    assert violations == [], format_violations(violations)
    assert csp_violations(page) == [], "\n".join(csp_violations(page))
