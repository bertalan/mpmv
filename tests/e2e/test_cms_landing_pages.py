"""E2E (CMS.4): term and vendor landing pages.

Done when: "A term page with its products". In the Wagtail admin an editor adds a term landing
page, chooses the catalog term in the chooser modal and publishes. The term's page
(`/{taxonomy}/{term}/`, D-07) then shows the editorial intro, the term's products by category and
the listing. A vendor landing page tells the vendor's story with its featured products, and the
storefront links to it. No CSP violation, and axe finds no WCAG 2.2 AA violation on either page.
"""

import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import totp_token
from apps.catalog.models import Category, Product
from apps.catalog.tests.factories import (
    CategoryFactory,
    CategoryTranslationFactory,
    ProductFactory,
    ProductTranslationFactory,
    TaxonomyFactory,
    TaxonomyTranslationFactory,
    TermFactory,
    TermTranslationFactory,
)
from apps.cms.models import HomePage, TermLandingPage, VendorLandingPage
from apps.core.models import TranslationStatus
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.login import verify_with_totp
from tests.e2e.test_cms_home_blocks import _axe_helper_style
from tests.e2e.test_cms_pages import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)

PUBLISHED = TranslationStatus.PUBLISHED


def _category(name: str) -> Category:
    category: Category = CategoryFactory.create()
    CategoryTranslationFactory.create(
        category=category, name=name, slug=name.lower(), status=PUBLISHED
    )
    return category


def _product(title: str, vendor: Vendor, category: Category | None = None) -> Product:
    product: Product = ProductFactory.create(
        vendor=vendor, status=Product.Status.PUBLISHED, category=category
    )
    slug = title.lower().replace(" ", "-")
    ProductTranslationFactory.create(product=product, title=title, slug=slug, status=PUBLISHED)
    return product


def test_an_editor_adds_a_term_landing_page_and_the_term_page_shows_it(
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
    vases, linen = _category("Vases"), _category("Linen")
    _product("Ceramic vase", vendor, vases).terms.add(term)
    _product("Linen napkins", vendor, linen).terms.add(term)
    home = HomePage.objects.get(locale__language_code="en")
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    page.goto(f"{live_server.url}/admin/login/")
    page.locator("input[name=username]").fill("editor@example.com")
    page.locator("input[name=password]").fill(password)
    page.get_by_role("button", name="Sign in").click()
    verify_with_totp(page, totp_token(device))
    page.goto(f"{live_server.url}/admin/pages/add/cms/termlandingpage/{home.pk}/")
    page.locator("#id_title").fill("Wedding gift guide")
    page.get_by_role("button", name="Choose a term").click()
    modal = page.locator(".modal")
    modal.get_by_role("link", name="occasion: Weddings").click()
    expect(modal).to_be_hidden()
    shot("admin-term-landing")
    page.get_by_role("button", name="More actions").click()
    page.get_by_role("button", name="Publish").click()
    expect(page.locator(".messages")).to_contain_text("published")
    others = [v for v in csp_violations(page) if not _axe_helper_style(v)]
    assert others == [], "\n".join(others)

    landing = TermLandingPage.objects.get(title="Wedding gift guide")
    assert landing.term_id == term.pk
    landing.body = [("paragraph", "<p>Gifts the couple will keep.</p>")]
    landing.save_revision().publish()

    page.goto(f"{live_server.url}/occasion/weddings/")
    expect(page.get_by_role("heading", level=1, name="Weddings")).to_be_visible()
    expect(page.get_by_text("Gifts the couple will keep.")).to_be_visible()
    by_category = page.locator("section.term-landing-category")
    expect(by_category).to_have_count(2)
    expect(by_category.first.get_by_role("link", name="Ceramic vase")).to_be_visible()
    expect(
        page.locator(".listing-results").get_by_role("link", name="Linen napkins")
    ).to_be_visible()
    shot("public-term-page")
    assert csp_violations(page) == [], "\n".join(csp_violations(page))
    violations = run_axe(page)
    assert violations == [], format_violations(violations)


def test_a_vendor_landing_page_tells_the_story_and_the_storefront_links_it(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE, slug="acme", display_name="Acme")
    _product("Copper kettle", vendor)
    home = HomePage.objects.get(locale__language_code="en")
    story = VendorLandingPage(
        title="The Acme workshop",
        slug="acme-workshop",
        vendor=vendor,
        body=[("paragraph", "<p>Three generations of copper work.</p>")],
    )
    home.add_child(instance=story)
    story.save_revision().publish()
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    page.goto(f"{live_server.url}/shop/acme/")
    page.get_by_role("link", name="Read the story of Acme").click()
    expect(page).to_have_url(f"{live_server.url}/acme-workshop/")
    expect(page.get_by_role("heading", level=1, name="The Acme workshop")).to_be_visible()
    expect(page.get_by_text("Three generations of copper work.")).to_be_visible()
    expect(page.get_by_role("link", name="Copper kettle")).to_be_visible()
    expect(page.get_by_role("link", name="See all products: Acme")).to_have_attribute(
        "href", "/shop/acme/"
    )
    shot("public-vendor-landing")
    assert csp_violations(page) == [], "\n".join(csp_violations(page))
    violations = run_axe(page)
    assert violations == [], format_violations(violations)
