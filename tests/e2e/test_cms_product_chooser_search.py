"""E2E (CMS.10, CMS.11): an editor searches the product chooser and places product cards.

In the Wagtail admin the editor adds a "Product cards" block, opens the product chooser, searches
by a brand name (the list narrows to that brand's product), picks it and publishes. The public
home page shows the card. No CSP violation on either side.
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
from apps.catalog.tests.factories import BrandFactory, ProductFactory, ProductTranslationFactory
from apps.cms.models import HomePage
from apps.core.models import TranslationStatus
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory
from tests.e2e.login import verify_with_totp
from tests.e2e.test_cms_home_blocks import _add_block, _axe_helper_style, _label
from tests.e2e.test_cms_pages import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)

PUBLISHED = TranslationStatus.PUBLISHED


def test_an_editor_searches_the_chooser_and_adds_product_cards(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    editor = UserFactory.create(email="editor@example.com", password=password, superuser=True)
    device = TOTPDeviceFactory.create(user=editor)
    vendor = VendorFactory.create(display_name="Pottery Studio", status=Vendor.Status.ACTIVE)
    kiln = BrandFactory.create(name="Kiln and Co")
    for sku, title, brand in (
        ("BWL-1", "Ceramic vase", kiln),
        ("JUG-1", "Linen napkins", None),
        ("CUP-1", "Tin cup", None),
    ):
        product = ProductFactory.create(
            sku=sku, vendor=vendor, status=Product.Status.PUBLISHED, brand=brand
        )
        ProductTranslationFactory.create(
            product=product, title=title, slug=title.lower().replace(" ", "-"), status=PUBLISHED
        )
    home = HomePage.objects.get(locale__language_code="en")
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    page.goto(f"{live_server.url}/admin/login/")
    page.locator("input[name=username]").fill("editor@example.com")
    page.locator("input[name=password]").fill(password)
    page.get_by_role("button", name="Sign in").click()
    verify_with_totp(page, totp_token(device))
    page.goto(f"{live_server.url}/admin/pages/{home.pk}/edit/")

    cards = _add_block(page, "Product cards")
    cards.get_by_role("textbox", name=_label("Heading")).fill("Picked for you")
    cards.get_by_role("button", name="Choose a product").click()
    modal = page.locator(".modal")
    expect(modal.get_by_role("link", name="JUG-1")).to_be_visible()
    modal.get_by_role("textbox", name="Search term").fill("kiln")
    expect(modal.get_by_role("link", name="BWL-1")).to_be_visible()
    expect(modal.get_by_role("link", name="JUG-1")).to_have_count(0)
    shot("admin-chooser-search")
    modal.get_by_role("link", name="BWL-1").click()
    expect(modal).to_be_hidden()

    page.get_by_role("button", name="More actions").click()
    page.get_by_role("button", name="Publish").click()
    expect(page.locator(".messages")).to_contain_text("published")

    page.goto(f"{live_server.url}/")
    expect(page.get_by_role("heading", name="Picked for you")).to_be_visible()
    expect(page.get_by_role("link", name="Ceramic vase")).to_be_visible()
    expect(page.get_by_text("Linen napkins")).to_have_count(0)
    shot("public-home")

    others = [v for v in csp_violations(page) if not _axe_helper_style(v)]
    assert others == [], "\n".join(others)
