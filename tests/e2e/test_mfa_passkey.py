"""E2E (CORE.4): a staff member enrols a passkey and signs in with it, in a real browser.

Chromium's WebAuthn virtual authenticator (DevTools protocol) stands in for the platform
authenticator, so `navigator.credentials.create/get` run for real against the server.
"""

import re
import secrets
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from playwright.sync_api import CDPSession, Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.models import PasskeyDevice
from apps.accounts.tests.factories import UserFactory
from tests.e2e.login import admin_login

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def virtual_authenticator(page: Page) -> Iterator[CDPSession]:
    session = page.context.new_cdp_session(page)
    session.send("WebAuthn.enable")
    session.send(
        "WebAuthn.addVirtualAuthenticator",
        {
            "options": {
                "protocol": "ctap2",
                "transport": "internal",
                "hasResidentKey": True,
                "hasUserVerification": True,
                "isUserVerified": True,
                "automaticPresenceSimulation": True,
            }
        },
    )
    yield session
    session.detach()


def _logged_in(page: Page) -> None:
    expect(
        page.get_by_role("link", name="Log out").or_(page.get_by_role("button", name="Log out"))
    ).to_be_visible()


def test_staff_enrol_a_passkey_and_sign_in_with_it(
    page: Page,
    live_server: LiveServer,
    virtual_authenticator: CDPSession,
    shot: Callable[[str], Path],
) -> None:
    password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    UserFactory.create(email="staff@example.com", password=password, staff=True)

    admin_login(page, live_server.url, "staff@example.com", password)
    expect(page).to_have_url(re.compile(r"/account/mfa/setup/"))
    expect(page.locator("form[data-passkey-ready]")).to_have_count(1)  # passkey.js is deferred
    page.get_by_role("button", name="Register a passkey").click()
    expect(page.locator("ol.recovery-codes li")).to_have_count(10)
    shot("passkey-registered")
    assert PasskeyDevice.objects.count() == 1

    page.get_by_role("link", name="Continue").click()
    _logged_in(page)

    page.context.clear_cookies()
    admin_login(page, live_server.url, "staff@example.com", password)
    expect(page).to_have_url(re.compile(r"/account/mfa/verify/"))
    shot("verify")
    expect(page.locator("form[data-passkey-ready]")).to_have_count(1)
    page.get_by_role("button", name="Use a passkey").click()
    _logged_in(page)
    shot("signed-in-with-passkey")
    assert PasskeyDevice.objects.get().last_used_at is not None


def _sign_in_with_a_new_passkey(page: Page, base_url: str, email: str, password: str) -> None:
    admin_login(page, base_url, email, password)
    expect(page.locator("form[data-passkey-ready]")).to_have_count(1)
    page.get_by_role("button", name="Register a passkey").click()
    expect(page.locator("ol.recovery-codes li")).to_have_count(10)
    page.context.clear_cookies()
    admin_login(page, base_url, email, password)
    expect(page.locator("form[data-passkey-ready]")).to_have_count(1)
    page.get_by_role("button", name="Use a passkey").click()
    page.wait_for_url(lambda url: "/account/mfa/verify" not in url)


def test_the_banner_opens_the_passkey_window(
    page: Page,
    live_server: LiveServer,
    virtual_authenticator: CDPSession,
    shot: Callable[[str], Path],
) -> None:
    """CORE.25: with no window open the banner holds the button; one touch opens the window."""
    password = secrets.token_urlsafe(16)
    UserFactory.create(
        email="owner@example.com",
        password=password,
        staff=True,
        permissions=["core.platform_owner", "core.elevate"],
    )
    _sign_in_with_a_new_passkey(page, live_server.url, "owner@example.com", password)

    page.goto(f"{live_server.url}/manager/platform/")
    banner = page.locator(".session-level--closed")
    expect(banner).to_contain_text("open a passkey window to cover several in a row")
    expect(banner.locator("form[data-passkey-ready]")).to_have_count(1)
    shot("banner-passkey-window-closed")
    banner.get_by_role("button", name="Open passkey window").click()
    expect(page.locator(".session-level--open")).to_be_visible()
    shot("banner-passkey-window-open")
    # A sensitive page that asked for a passkey now passes without the prompt.
    page.goto(f"{live_server.url}/django-admin/accounts/user/")
    expect(page.get_by_text("This needs your passkey")).to_have_count(0)


def test_the_security_page_shows_the_passkey(
    page: Page,
    live_server: LiveServer,
    virtual_authenticator: CDPSession,
    shot: Callable[[str], Path],
) -> None:
    password = secrets.token_urlsafe(16)
    UserFactory.create(email="staff2@example.com", password=password, staff=True)
    _sign_in_with_a_new_passkey(page, live_server.url, "staff2@example.com", password)

    page.goto(f"{live_server.url}/account/mfa/setup/?from=security")
    expect(page.get_by_text("Your account is protected with a passkey")).to_be_visible()
    expect(page.get_by_text("Your role requires a second factor")).to_have_count(0)
    expect(page.get_by_role("button", name="Add another passkey")).to_be_visible()
    shot("security-page-with-passkey")
