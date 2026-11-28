"""E2E (I18N.11 sweep): the real switcher, clicked in a browser, on one page of each class.

The integration sweep (`tests/integration/test_language_switcher_everywhere.py`) covers every
route class by posting the form it reads from the page; this one clicks the button, so a CSP
violation, a hidden button or a broken form is caught too. Screenshots in
build/e2e/screenshots. The signed-in Manager and account cases changed language through
lane-b/i18n-manager-language lands: remove the marker then.
"""

import html
import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from django.test import Client
from django.utils import translation
from django.utils.translation import gettext
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.cms.seed import seed_text
from apps.cms.tests.factories import StandardPageFactory
from apps.core.i18n import configured_languages, primary_language
from apps.vendors.tests.factories import VendorMemberFactory
from tests.e2e.test_cms_pages import RECORD_CSP_VIOLATIONS, csp_violations
from tests.i18n import fallback_notice, language_prefix, other_language

pytestmark = pytest.mark.django_db(transaction=True)


def switch_notice(language: str) -> str:
    """The switch notice as the page shows it in `language` (the catalogs translate it)."""
    with translation.override(language):
        return gettext("This content is not available in this language.")


def _about(code: str) -> str:
    """The path of the seeded About page in `code` (`/about/`, `/it/chi-siamo/`, ...)."""
    return f"{language_prefix(code)}/{seed_text(code).about.slug}/"


def _switch(page: Page, code: str) -> None:
    page.locator(f'nav.language-switcher button[value="{code}"]').click()


def _lang(page: Page, code: str) -> None:
    expect(page.locator("html")).to_have_attribute("lang", code)


def test_a_translated_cms_page_keeps_its_object(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    page.goto(f"{live_server.url}{_about(primary_language())}")
    shot(primary_language())
    others = [code for code in configured_languages() if code != primary_language()]
    if not others:
        pytest.skip("needs a second configured language")
    for code in others:
        _switch(page, code)
        expect(page).to_have_url(f"{live_server.url}{_about(code)}")
        _lang(page, code)
        expect(page.locator(".site-messages")).to_have_count(0)
        shot(code)
    assert csp_violations(page) == []


def test_an_untranslated_page_stays_in_place_with_the_fallback_notice(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    second = other_language()
    StandardPageFactory.create(title="Only in English").save_revision().publish()
    page.goto(f"{live_server.url}/only-in-english/")
    shot(primary_language())
    _switch(page, second)
    # I18N.22: the same page under the new prefix, showing the primary-language text with the
    # layout's own notice (not a flash), so it is still there after a reload.
    expect(page).to_have_url(f"{live_server.url}{language_prefix(second)}/only-in-english/")
    _lang(page, second)
    expect(page.locator(".site-messages")).to_contain_text(
        html.unescape(fallback_notice(primary_language(), second))
    )
    shot(f"{second}-notice")
    page.reload()
    expect(page.locator(".site-messages")).to_have_count(1)


def test_a_secret_url_is_not_echoed_and_the_switch_goes_home(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    second = other_language()
    key = secrets.token_hex(16)
    page.goto(f"{live_server.url}/vendor/claim/{key}/")
    assert key not in page.content().replace(page.url, "")
    shot(primary_language())
    _switch(page, second)
    expect(page).to_have_url(f"{live_server.url}{language_prefix(second)}/")
    _lang(page, second)
    assert key not in page.content()
    shot(f"{second}-home")


def test_a_page_without_an_object_keeps_its_own_path(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page.goto(f"{live_server.url}{_about(other_language())}?utm_source=x")
    _switch(page, primary_language())
    expect(page).to_have_url(f"{live_server.url}{_about(primary_language())}?utm_source=x")
    _lang(page, primary_language())
    shot(primary_language())


def test_the_manager_switch_changes_the_language(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    owner = VendorMemberFactory.create(role="owner").user
    client = Client()
    verified_login(client, owner)
    page.context.add_cookies(
        [
            {
                "name": "sessionid",
                "value": client.cookies["sessionid"].value,
                "url": live_server.url,
            }
        ]
    )
    second = other_language()
    page.goto(f"{live_server.url}/manager/shop/")
    shot(primary_language())
    _switch(page, second)
    expect(page).to_have_url(f"{live_server.url}/manager/shop/")
    _lang(page, second)
    shot(second)


def test_the_account_switch_changes_the_language(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    user = UserFactory.create()
    client = Client()
    client.force_login(user)
    page.context.add_cookies(
        [{"name": "sessionid", "value": client.cookies["sessionid"].value, "url": live_server.url}]
    )
    second = other_language()
    page.goto(f"{live_server.url}/account/mfa/")
    _switch(page, second)
    _lang(page, second)
    shot(second)


def test_an_anonymous_visitor_switches_language_on_the_sign_in_page(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    """The choice lives in the session (no cookie) and holds on the next unprefixed page."""
    second = other_language()
    page.goto(f"{live_server.url}/account/login/")
    _switch(page, second)
    expect(page).to_have_url(f"{live_server.url}/account/login/")
    _lang(page, second)
    shot(second)
    assert not [c for c in page.context.cookies() if c["name"] == "django_language"]
    page.goto(f"{live_server.url}/account/login/")
    _lang(page, second)
