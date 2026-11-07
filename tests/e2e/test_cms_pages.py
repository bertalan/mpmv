"""E2E (CMS.1): EN/IT home and static pages, per-language navigation, Wagtail admin under CSP.

Done when: "EN/IT home and static pages". The seed content (`manage.py seed_cms`) is loaded
for every e2e test by the suite's conftest.
"""

import re
import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import totp_token
from apps.cms.models import NavigationMenu, StandardPage
from tests.e2e.login import verify_with_totp

pytestmark = pytest.mark.django_db(transaction=True)

RECORD_CSP_VIOLATIONS = """
window.__cspViolations = [];
document.addEventListener("securitypolicyviolation", (event) => {
  window.__cspViolations.push(
    `${location.pathname}: ${event.effectiveDirective} blocked ${event.blockedURI} `
    + `(${event.sourceFile}:${event.lineNumber} ${event.sample})`
  );
});
"""


def csp_violations(page: Page) -> list[str]:
    violations: list[str] = page.evaluate("window.__cspViolations")
    return violations


def test_visitor_reads_home_and_static_pages_in_english_and_italian(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    page.goto(f"{live_server.url}/")
    expect(page.locator("html")).to_have_attribute("lang", "en")
    expect(page.get_by_role("heading", level=1)).to_have_text("Home")
    shot("en-home")

    page.get_by_role("navigation", name="Main").get_by_role("link", name="About us").click()
    expect(page).to_have_url(re.compile(r"/about/$"))
    expect(page.get_by_role("heading", level=1)).to_have_text("About us")
    shot("en-about")

    page.locator('button[value="it"]').click()
    expect(page).to_have_url(re.compile(r"/it/chi-siamo/$"))
    expect(page.locator("html")).to_have_attribute("lang", "it")
    expect(page.get_by_role("heading", level=1)).to_have_text("Chi siamo")
    shot("it-about")

    page.get_by_role("contentinfo").get_by_role("link", name="Home").click()
    expect(page).to_have_url(re.compile(r"/it/$"))
    expect(page.get_by_text("Prodotti di artigiani indipendenti")).to_be_visible()
    shot("it-home")

    assert csp_violations(page) == [], "\n".join(csp_violations(page))


def test_editor_opens_the_wagtail_admin_under_strict_csp(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    user = UserFactory.create(email="editor@example.com", password=password, superuser=True)
    device = TOTPDeviceFactory.create(user=user)
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    response = page.goto(f"{live_server.url}/admin/login/")
    assert response is not None
    assert "'unsafe-inline'" not in response.headers["content-security-policy"]
    page.locator("input[name=username]").fill("editor@example.com")
    page.locator("input[name=password]").fill(password)
    page.get_by_role("button", name="Sign in").click()
    verify_with_totp(page, totp_token(device))
    page.goto(f"{live_server.url}/admin/pages/")
    expect(page.get_by_role("link", name="Home").first).to_be_visible()
    shot("admin-pages")

    # The Italian header menu: a StreamField of links, edited without inline scripts.
    menu = NavigationMenu.objects.get(locale__language_code="it", location="header")
    page.goto(f"{live_server.url}/admin/snippets/cms/navigationmenu/edit/{menu.pk}/")
    expect(page.get_by_text("Chi siamo").first).to_be_visible()
    shot("admin-it-menu")

    assert csp_violations(page) == [], "\n".join(csp_violations(page))

    about = StandardPage.objects.get(locale__language_code="it")
    page.goto(f"{live_server.url}/admin/pages/{about.pk}/edit/")
    expect(page.locator("#id_title")).to_have_value("Chi siamo")
    expect(page.get_by_role("button", name="Save draft")).to_be_visible()
    shot("admin-it-page")
    # Known: the editor's built-in accessibility checker (axe-core, in Wagtail's vendor.js)
    # adds a helper <style> element, which the CSP blocks; editing is not affected.
    others = [v for v in csp_violations(page) if not _axe_helper_style(v)]
    assert others == [], "\n".join(others)


def _axe_helper_style(violation: str) -> bool:
    return "style-src-elem blocked inline" in violation and "wagtailadmin/js/vendor.js" in violation
