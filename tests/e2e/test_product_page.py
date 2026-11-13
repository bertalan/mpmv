"""E2E (CAT.7): a product page in a real browser, with its screenshot.

Done when: the page shows one h1, the SKU, the vendor link, the default variant selected and the
net price, carries a Product JSON-LD node, and triggers no CSP violation.
"""

import json
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.catalog.models import Product
from apps.catalog.tests.factories import (
    ProductFactory,
    ProductTranslationFactory,
    VariantFactory,
    VariantTranslationFactory,
)
from apps.core.models import TranslationStatus
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory
from tests.e2e.test_cms_pages import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)

PUBLISHED = TranslationStatus.PUBLISHED


@pytest.fixture
def product_page() -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE, display_name="Rossi Ceramics")
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
        ("en", "Hand-made mug", "hand-made-mug", "Thrown and glazed by hand."),
        ("it", "Tazza fatta a mano", "tazza-fatta-a-mano", "Tornita e smaltata a mano."),
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
            variant=variant, language="en", label=label, status=PUBLISHED
        )


@pytest.mark.usefixtures("product_page")
@pytest.mark.parametrize(
    ("path", "language", "title"),
    [
        ("/product/hand-made-mug/", "en", "Hand-made mug"),
        ("/it/prodotto/tazza-fatta-a-mano/", "it", "Tazza fatta a mano"),
    ],
)
def test_a_product_page_shows_its_content_and_structured_data(
    page: Page,
    live_server: LiveServer,
    shot: Callable[[str], Path],
    path: str,
    language: str,
    title: str,
) -> None:
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
    assert next(node for node in graph if node["@type"] == "Product")["name"] == title
    assert csp_violations(page) == [], "\n".join(csp_violations(page))
