"""E2E (I18N.11 sweep): the real switcher, clicked in a browser, on one page of each class.

The integration sweep (`tests/integration/test_language_switcher_everywhere.py`) covers every
route class by posting the form it reads from the page; this one clicks the button, so a CSP
violation, a hidden button or a broken form is caught too. Screenshots in
build/e2e/screenshots. The signed-in Manager and account cases changed language through
lane-b/i18n-manager-language lands: remove the marker then.
"""

import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from django.test import Client
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.cms.tests.factories import StandardPageFactory
from apps.vendors.tests.factories import VendorMemberFactory
from tests.e2e.test_cms_pages import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)

NOTICE = "not available in this language"


def _lang(page: Page, code: str) -> None:
    expect(page.locator("html")).to_have_attribute("lang", code)


def test_a_translated_cms_page_keeps_its_object(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    page.goto(f"{live_server.url}/about/")
    shot("en")
    page.locator('nav.language-switcher button[value="it"]').click()
    expect(page).to_have_url(f"{live_server.url}/it/chi-siamo/")
    _lang(page, "it")
    expect(page.locator(".site-messages")).to_have_count(0)
    shot("it")
    page.locator('nav.language-switcher button[value="de"]').click()
    expect(page).to_have_url(f"{live_server.url}/de/ueber-uns/")
    _lang(page, "de")
    shot("de")
    assert csp_violations(page) == []


def test_an_untranslated_page_falls_back_to_the_home_with_a_notice(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    StandardPageFactory.create(title="Only in English").save_revision().publish()
    page.goto(f"{live_server.url}/only-in-english/")
    shot("en")
    page.locator('nav.language-switcher button[value="fr"]').click()
    expect(page).to_have_url(f"{live_server.url}/fr/")
    _lang(page, "fr")
    expect(page.locator(".site-messages")).to_contain_text(NOTICE)
    shot("fr-notice")
    page.reload()
    expect(page.locator(".site-messages")).to_have_count(0)


def test_a_secret_url_is_not_echoed_and_the_switch_goes_home(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    key = secrets.token_hex(16)
    page.goto(f"{live_server.url}/vendor/claim/{key}/")
    assert key not in page.content().replace(page.url, "")
    shot("en")
    page.locator('nav.language-switcher button[value="es"]').click()
    expect(page).to_have_url(f"{live_server.url}/es/")
    _lang(page, "es")
    assert key not in page.content()
    shot("es-home")


def test_a_page_without_an_object_keeps_its_own_path(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page.goto(f"{live_server.url}/it/chi-siamo/?utm_source=x")
    page.locator('nav.language-switcher button[value="en"]').click()
    expect(page).to_have_url(f"{live_server.url}/about/?utm_source=x")
    _lang(page, "en")
    shot("en")


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
    page.goto(f"{live_server.url}/manager/shop/")
    shot("en")
    page.locator('nav.language-switcher button[value="it"]').click()
    expect(page).to_have_url(f"{live_server.url}/manager/shop/")
    _lang(page, "it")
    shot("it")


def test_the_account_switch_changes_the_language(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    user = UserFactory.create()
    client = Client()
    client.force_login(user)
    page.context.add_cookies(
        [{"name": "sessionid", "value": client.cookies["sessionid"].value, "url": live_server.url}]
    )
    page.goto(f"{live_server.url}/account/mfa/")
    page.locator('nav.language-switcher button[value="de"]').click()
    _lang(page, "de")
    shot("de")


def test_an_anonymous_visitor_switches_language_on_the_sign_in_page(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    """The choice lives in the session (no cookie) and holds on the next unprefixed page."""
    page.goto(f"{live_server.url}/account/login/")
    page.locator('nav.language-switcher button[value="de"]').click()
    expect(page).to_have_url(f"{live_server.url}/account/login/")
    _lang(page, "de")
    shot("de")
    assert not [c for c in page.context.cookies() if c["name"] == "django_language"]
    page.goto(f"{live_server.url}/account/login/")
    _lang(page, "de")
