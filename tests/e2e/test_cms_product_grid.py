"""E2E (CMS.10): an editor adds a product grid filtered by a term to the home page.

Done when: "An editor places a product grid filtered by a term on a page and it renders the CAT.8
results". In the Wagtail admin the editor adds a "Product grid" block, chooses a catalog term in
the chooser modal and publishes. The public home page shows the term's published products as
product cards and a "See all" link to the term's listing page. No CSP violation on either side.
"""

import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import totp_token
from apps.catalog.models import Product
from apps.catalog.tests.factories import (
    ProductFactory,
    ProductTranslationFactory,
    TaxonomyFactory,
    TaxonomyTranslationFactory,
    TermFactory,
    TermTranslationFactory,
)
from apps.cms.models import HomePage
from apps.core.models import TranslationStatus
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory
from tests.e2e.login import verify_with_totp
from tests.e2e.test_cms_home_blocks import _add_block, _axe_helper_style, _label
from tests.e2e.test_cms_pages import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)

PUBLISHED = TranslationStatus.PUBLISHED


def test_an_editor_adds_a_term_product_grid_to_the_home_page(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    editor = UserFactory.create(email="editor@example.com", password=password, superuser=True)
    device = TOTPDeviceFactory.create(user=editor)
    taxonomy = TaxonomyFactory.create(code="occasion", hierarchical=False, has_landing=True)
    TaxonomyTranslationFactory.create(
        taxonomy=taxonomy, language="en", name="Occasion", slug="occasion", status=PUBLISHED
    )
    term = TermFactory.create(taxonomy=taxonomy)
    TermTranslationFactory.create(
        term=term, language="en", name="Weddings", slug="weddings", status=PUBLISHED
    )
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    for title, status in (
        ("Ceramic vase", Product.Status.PUBLISHED),
        ("Linen napkins", Product.Status.PUBLISHED),
        ("Secret draft", Product.Status.DRAFT),
    ):
        product = ProductFactory.create(vendor=vendor, status=status)
        slug = title.lower().replace(" ", "-")
        ProductTranslationFactory.create(product=product, title=title, slug=slug, status=PUBLISHED)
        product.terms.add(term)
    home = HomePage.objects.get(locale__language_code="en")
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    page.goto(f"{live_server.url}/admin/login/")
    page.locator("input[name=username]").fill("editor@example.com")
    page.locator("input[name=password]").fill(password)
    page.get_by_role("button", name="Sign in").click()
    verify_with_totp(page, totp_token(device))
    page.goto(f"{live_server.url}/admin/pages/{home.pk}/edit/")

    grid = _add_block(page, "Product grid")
    grid.get_by_role("textbox", name=_label("Heading")).fill("Wedding gifts")
    sources = page.locator('input[name$="-value-source-count"]')
    grid.get_by_role("button", name="Insert a block").last.click()
    page.get_by_role("option", name="Term", exact=True).click()
    expect(sources).to_have_value("1")
    grid.get_by_role("button", name="Choose a term").click()
    modal = page.locator(".modal")
    modal.get_by_role("link", name="occasion: Weddings").click()
    expect(modal).to_be_hidden()
    shot("admin-grid")

    page.get_by_role("button", name="More actions").click()
    page.get_by_role("button", name="Publish").click()
    expect(page.locator(".messages")).to_contain_text("published")

    page.goto(f"{live_server.url}/")
    expect(page.get_by_role("heading", name="Wedding gifts")).to_be_visible()
    expect(page.get_by_role("link", name="Ceramic vase")).to_have_attribute(
        "href", "/product/ceramic-vase/"
    )
    expect(page.get_by_role("link", name="Linen napkins")).to_be_visible()
    tile = page.locator("section.block-product-grid article.product-tile").first
    expect(tile).to_have_css("border-top-style", "solid")  # the base theme styles the tiles
    expect(page.get_by_text("Secret draft")).to_have_count(0)
    expect(page.get_by_role("link", name="See all products: Weddings")).to_have_attribute(
        "href", "/occasion/weddings/"
    )
    shot("public-home")

    others = [v for v in csp_violations(page) if not _axe_helper_style(v)]
    assert others == [], "\n".join(others)
