"""E2E (CMS.2 part 1/2): an editor builds the home page from blocks in the Wagtail admin.

Done when: "An editor builds the home page without code". The editor adds a hero with an image
and its alt text, a call to action and a FAQ to the EN home page, publishes it, and the public
page shows them: the FAQ answers open as disclosures. No CSP violation on either side.
"""

import re
import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Locator, Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import totp_token
from apps.cms.models import HomePage
from apps.core.tests.factories import MediaImageFactory
from tests.e2e.login import verify_with_totp
from tests.e2e.test_cms_pages import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)


def _axe_helper_style(violation: str) -> bool:
    """Known since CMS.1: Wagtail's accessibility checker adds a <style> the CSP blocks."""
    return "style-src-elem blocked inline" in violation and "wagtailadmin/js/vendor.js" in violation


def _add_block(page: Page, label: str) -> Locator:
    """Append a block of type `label` to the body and return its form."""
    body = page.locator("#panel-child-content-body-section")
    body.get_by_role("button", name="Insert a block").last.click()
    page.get_by_role("option", name=label, exact=True).click()
    block = body.locator("[data-contentpath][data-streamfield-child]").last
    expect(block).to_be_visible()
    return block


def test_an_editor_builds_the_home_page_with_hero_cta_and_faq(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    editor = UserFactory.create(email="editor@example.com", password=password, superuser=True)
    device = TOTPDeviceFactory.create(user=editor)
    MediaImageFactory.create(title="Workshop banner", file__filename="banner.png")
    home = HomePage.objects.get(locale__language_code="en")
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    page.goto(f"{live_server.url}/admin/login/")
    page.locator("input[name=username]").fill("editor@example.com")
    page.locator("input[name=password]").fill(password)
    page.get_by_role("button", name="Sign in").click()
    verify_with_totp(page, totp_token(device))
    page.goto(f"{live_server.url}/admin/pages/{home.pk}/edit/")

    hero = _add_block(page, "Hero")
    hero.get_by_label("Heading").fill("Handmade goods")
    hero.get_by_label("Text").fill("From small workshops.")
    hero.get_by_role("button", name="Choose an image").click()
    modal = page.locator(".modal")
    modal.get_by_text("Workshop banner").click()
    expect(modal).to_be_hidden()
    hero.get_by_label("Alt text").fill("A potter at the wheel")

    cta = _add_block(page, "Call to action")
    cta.get_by_label("Heading").fill("Sell with us")
    cta.get_by_label("URL").fill("https://example.com/apply/")
    cta.get_by_label("Label").fill("Apply")

    faq = _add_block(page, "FAQ")
    faq.get_by_label("Heading").fill("Questions")
    faq.get_by_label("Question").fill("Do you ship abroad?")
    faq.locator("[contenteditable=true]").first.fill("Yes, to most countries.")
    shot("admin-blocks")

    page.get_by_role("button", name="More actions").click()
    page.get_by_role("button", name="Publish").click()
    expect(page.locator(".messages")).to_contain_text("published")

    page.goto(f"{live_server.url}/")
    expect(page.get_by_role("heading", name="Handmade goods")).to_be_visible()
    expect(page.get_by_role("img", name="A potter at the wheel")).to_have_attribute(
        "src", re.compile("banner")
    )
    expect(page.get_by_role("link", name="Apply")).to_have_attribute(
        "href", "https://example.com/apply/"
    )
    answer = page.get_by_text("Yes, to most countries.")
    expect(answer).to_be_hidden()
    page.get_by_text("Do you ship abroad?").click()
    expect(answer).to_be_visible()
    shot("public-home")

    others = [v for v in csp_violations(page) if not _axe_helper_style(v)]
    assert others == [], "\n".join(others)
