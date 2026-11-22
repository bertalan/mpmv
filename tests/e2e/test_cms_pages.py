"""E2E (CMS.1): home and static pages, per-language navigation, Wagtail admin under CSP.

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
from apps.cms.seed import seed_text
from apps.core.i18n import primary_language
from tests.e2e.login import verify_with_totp
from tests.i18n import language_prefix, other_language, translated

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


def test_visitor_reads_home_and_static_pages_in_two_languages(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    primary, other = primary_language(), other_language()
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    page.goto(f"{live_server.url}/")
    expect(page.locator("html")).to_have_attribute("lang", primary)
    expect(page.get_by_role("heading", level=1)).to_have_text(translated("Home"))
    shot(f"{primary}-home")

    about = seed_text(primary).about
    page.get_by_role("navigation", name=translated("Main")).get_by_role(
        "link", name=about.title
    ).click()
    expect(page).to_have_url(re.compile(rf"/{about.slug}/$"))
    expect(page.get_by_role("heading", level=1)).to_have_text(about.title)
    shot(f"{primary}-about")

    texts = seed_text(other)
    page.locator(f'button[value="{other}"]').click()
    expect(page).to_have_url(re.compile(rf"{language_prefix(other)}/{texts.about.slug}/$"))
    expect(page.locator("html")).to_have_attribute("lang", other)
    expect(page.get_by_role("heading", level=1)).to_have_text(texts.about.title)
    shot(f"{other}-about")

    page.get_by_role("contentinfo").get_by_role("link", name=translated("Home", other)).click()
    expect(page).to_have_url(re.compile(rf"{language_prefix(other)}/$"))
    expect(page.get_by_text(re.sub("<[^>]+>", "", texts.home.body))).to_be_visible()
    shot(f"{other}-home")

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
    page.get_by_role("button", name=translated("Sign in")).click()
    verify_with_totp(page, totp_token(device))
    page.goto(f"{live_server.url}/admin/pages/")
    expect(page.get_by_role("link", name=translated("Home")).first).to_be_visible()
    shot("admin-pages")

    # A second language's header menu: a StreamField of links, edited without inline scripts.
    other = other_language()
    title = seed_text(other).about.title
    menu = NavigationMenu.objects.get(locale__language_code=other, location="header")
    page.goto(f"{live_server.url}/admin/snippets/cms/navigationmenu/edit/{menu.pk}/")
    expect(page.get_by_text(title).first).to_be_visible()
    shot("admin-other-menu")

    assert csp_violations(page) == [], "\n".join(csp_violations(page))

    about = StandardPage.objects.get(locale__language_code=other)
    page.goto(f"{live_server.url}/admin/pages/{about.pk}/edit/")
    expect(page.locator("#id_title")).to_have_value(title)
    expect(page.get_by_role("button", name=translated("Save draft"))).to_be_visible()
    shot("admin-other-page")
    # Known: the editor's built-in accessibility checker (axe-core, in Wagtail's vendor.js)
    # adds a helper <style> element, which the CSP blocks; editing is not affected.
    others = [v for v in csp_violations(page) if not _axe_helper_style(v)]
    assert others == [], "\n".join(others)


def _axe_helper_style(violation: str) -> bool:
    return "style-src-elem blocked inline" in violation and "wagtailadmin/js/vendor.js" in violation
