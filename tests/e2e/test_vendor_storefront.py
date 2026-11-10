"""E2E (VEN.10): the public vendor storefront in Italian.

Done when: "Category sidebar correct in IT too". The storefront shows the vendor's Italian bio,
policies and its own categories named in Italian; choosing a category narrows the products.
No CSP violation, and axe finds no WCAG 2.2 AA violation.
"""

from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.catalog.models import Product
from apps.catalog.tests.factories import (
    CategoryFactory,
    CategoryTranslationFactory,
    ProductFactory,
    ProductTranslationFactory,
)
from apps.core.models import TranslationStatus
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorTranslationFactory
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.test_cms_pages import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)

PUBLISHED = TranslationStatus.PUBLISHED


def _product(title: str, vendor: Vendor, category: object) -> None:
    product = ProductFactory.create(
        vendor=vendor, status=Product.Status.PUBLISHED, category=category
    )
    for language in ("en", "it"):
        ProductTranslationFactory.create(
            product=product,
            language=language,
            title=title,
            slug=title.lower().replace(" ", "-"),
            status=PUBLISHED,
        )


def test_the_italian_storefront_shows_bio_policies_and_categories(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE, slug="acme", display_name="Acme")
    for language, tagline, bio, policy in (
        ("en", "Copper since 1950", "Our English story.", "Ships in two days."),
        ("it", "Rame dal 1950", "La nostra storia.", "Spedizione in due giorni."),
    ):
        VendorTranslationFactory.create(
            vendor=vendor,
            language=language,
            status=PUBLISHED,
            tagline=tagline,
            description=bio,
            shipping_policy=policy,
        )
    kettles = CategoryFactory.create()
    pans = CategoryFactory.create()
    for category, en, it in ((kettles, "Kettles", "Bollitori"), (pans, "Pans", "Padelle")):
        for language, name in (("en", en), ("it", it)):
            CategoryTranslationFactory.create(
                category=category, language=language, name=name, slug=name.lower(), status=PUBLISHED
            )
    _product("Bollitore rame", vendor, kettles)
    _product("Padella rame", vendor, pans)
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    page.goto(f"{live_server.url}/it/shop/acme/")
    expect(page.get_by_text("Rame dal 1950")).to_be_visible()
    expect(page.get_by_text("La nostra storia.")).to_be_visible()
    page.locator("summary").first.click()
    expect(page.get_by_text("Spedizione in due giorni.")).to_be_visible()
    nav = page.get_by_role("navigation", name="Store categories")
    expect(nav.get_by_role("link", name="Bollitori")).to_be_visible()
    expect(nav.get_by_role("link", name="Padelle")).to_be_visible()
    shot("public-vendor-storefront-it")
    nav.get_by_role("link", name="Bollitori").click()
    expect(page).to_have_url(f"{live_server.url}/it/shop/acme/?category=bollitori")
    expect(page.locator(".product-grid li")).to_have_count(1)
    assert csp_violations(page) == [], "\n".join(csp_violations(page))
    violations = run_axe(page)
    assert violations == [], format_violations(violations)
