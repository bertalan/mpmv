"""E2E (CMS.2 part 2/2): an editor adds a term card and a vendor card to the home page.

Done when: "An editor builds the home page without code". In the Wagtail admin the editor adds a
"Featured links" block, chooses a catalog term and an active vendor in the chooser modals (no
typed URLs) and publishes. The public home page links to the term's taxonomy URL and to the
vendor's storefront (spec 03 "URLs"). No CSP violation on either side.
"""

import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import totp_token
from apps.catalog.tests.factories import (
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


def test_an_editor_adds_a_term_card_and_a_vendor_card_to_the_home_page(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    editor = UserFactory.create(email="editor@example.com", password=password, superuser=True)
    device = TOTPDeviceFactory.create(user=editor)
    taxonomy = TaxonomyFactory.create(code="occasion", hierarchical=False)
    TaxonomyTranslationFactory.create(
        taxonomy=taxonomy, language="en", name="Occasion", slug="occasion", status=PUBLISHED
    )
    term = TermFactory.create(taxonomy=taxonomy)
    TermTranslationFactory.create(
        term=term, language="en", name="Weddings", slug="weddings", status=PUBLISHED
    )
    VendorFactory.create(
        display_name="Pottery Studio", slug="pottery-studio", status=Vendor.Status.ACTIVE
    )
    home = HomePage.objects.get(locale__language_code="en")
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    page.goto(f"{live_server.url}/admin/login/")
    page.locator("input[name=username]").fill("editor@example.com")
    page.locator("input[name=password]").fill(password)
    page.get_by_role("button", name="Sign in").click()
    verify_with_totp(page, totp_token(device))
    page.goto(f"{live_server.url}/admin/pages/{home.pk}/edit/")

    featured = _add_block(page, "Featured links")
    featured.get_by_role("textbox", name=_label("Heading")).fill("Start here")
    modal = page.locator(".modal")

    cards = page.locator('input[name$="-value-cards-count"]')
    featured.get_by_role("button", name="Insert a block").last.click()
    page.get_by_role("option", name="Term", exact=True).click()
    expect(cards).to_have_value("1")
    featured.get_by_role("button", name="Choose a term").click()
    modal.get_by_role("link", name="occasion: Weddings").click()
    expect(modal).to_be_hidden()

    featured.get_by_role("button", name="Insert a block").last.click()
    page.get_by_role("option", name="Vendor", exact=True).click()
    expect(cards).to_have_value("2")
    featured.get_by_role("button", name="Choose a vendor").click()
    modal.get_by_role("link", name="Pottery Studio").click()
    expect(modal).to_be_hidden()
    shot("admin-cards")

    page.get_by_role("button", name="More actions").click()
    page.get_by_role("button", name="Publish").click()
    expect(page.locator(".messages")).to_contain_text("published")

    page.goto(f"{live_server.url}/")
    expect(page.get_by_role("heading", name="Start here")).to_be_visible()
    expect(page.get_by_role("link", name="Weddings")).to_have_attribute(
        "href", "/occasion/weddings/"
    )
    expect(page.get_by_role("link", name="Pottery Studio")).to_have_attribute(
        "href", "/shop/pottery-studio/"
    )
    shot("public-home")

    others = [v for v in csp_violations(page) if not _axe_helper_style(v)]
    assert others == [], "\n".join(others)
