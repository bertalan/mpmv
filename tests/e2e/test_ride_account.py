"""E2E (THM.8 2/2): the customer area and the sign-in pages with the `ride` theme.

A customer signs in with the keyboard only (password, then authenticator code), opens My orders
and the sessions page; each account page renders with the ride tokens, passes axe (`wcag22aa`),
raises no CSP violation and never scrolls sideways at 360, 768 and 1280 px.
"""

import re
import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from django.test import Client
from playwright.sync_api import Page, expect
from pytest_django.fixtures import Settings as SettingsWrapper
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import totp_token
from apps.orders.tests.integration.test_retention import place
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)

NO_SIDEWAYS_SCROLL = "() => document.documentElement.scrollWidth <= window.innerWidth"


@pytest.fixture(autouse=True)
def _ride(settings: SettingsWrapper, live_server: LiveServer, _cms_site: None) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"


def _check(page: Page, width: int) -> None:
    expect(page.locator("html")).to_have_attribute("data-theme", "ride")
    panel = page.locator(".account-page")
    expect(panel).to_have_count(1)
    style = panel.evaluate(
        "e => { const s = getComputedStyle(e); return {w: s.maxWidth, top: s.borderTopWidth}; }"
    )
    assert style["top"] == "4px", style  # the ride accent bar
    assert style["w"] != "none", style
    assert page.evaluate(NO_SIDEWAYS_SCROLL), f"sideways scroll at {width}px"
    violations = run_axe(page)
    assert violations == [], format_violations(violations)
    assert csp_violations(page) == [], "\n".join(csp_violations(page))


def _signed_in_page(page: Page, live_server: LiveServer) -> None:
    user = UserFactory.create(password=secrets.token_urlsafe(16))
    client = Client()
    client.force_login(user)
    page.context.add_cookies(
        [{"name": "sessionid", "value": client.cookies["sessionid"].value, "url": live_server.url}]
    )
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    place(customer=user, state="awaiting_payment")


@pytest.mark.parametrize("width", [360, 768, 1280])
def test_the_customer_area_renders_with_ride(
    width: int, page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    _signed_in_page(page, live_server)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{live_server.url}/account/orders/")
    expect(page.get_by_role("heading", level=1, name="My orders")).to_be_visible()
    _check(page, width)
    shot(f"{width}-orders")
    page.locator("tbody a").first.click()
    expect(page.locator(".timeline")).to_be_visible()
    _check(page, width)
    shot(f"{width}-order")
    page.goto(f"{live_server.url}/account/sessions/")
    expect(page.get_by_role("heading", level=1, name="Your sessions")).to_be_visible()
    _check(page, width)
    shot(f"{width}-sessions")
    page.goto(f"{live_server.url}/account/privacy/")
    _check(page, width)
    page.goto(f"{live_server.url}/account/mfa/setup/")
    _check(page, width)


@pytest.mark.parametrize("width", [360, 1280])
def test_a_customer_signs_in_with_the_keyboard_only(
    width: int, page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    password = secrets.token_urlsafe(16)
    user = UserFactory.create(email="rider@example.com", password=password)
    device = TOTPDeviceFactory.create(user=user)
    place(customer=user, state="awaiting_payment")
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{live_server.url}/account/login/")
    _check(page, width)
    shot(f"{width}-login")

    expect(page.get_by_label("Email")).to_be_focused()  # autofocus: first stop for typing
    page.keyboard.type("rider@example.com")
    page.keyboard.press("Tab")
    expect(page.get_by_label("Password")).to_be_focused()
    page.keyboard.type(password)
    page.keyboard.press("Enter")

    expect(page).to_have_url(re.compile(r"/account/mfa/verify/"))
    _check(page, width)
    shot(f"{width}-verify")
    page.get_by_label("Authentication code").focus()
    page.keyboard.type(totp_token(device))
    page.keyboard.press("Enter")
    page.wait_for_url(lambda url: "/account/mfa/verify" not in url)

    page.goto(f"{live_server.url}/account/orders/")
    expect(page.get_by_role("heading", level=1, name="My orders")).to_be_visible()
    _check(page, width)
    page.goto(f"{live_server.url}/account/sessions/")
    expect(page.get_by_role("heading", level=1, name="Your sessions")).to_be_visible()
    expect(page.get_by_role("button", name="Sign out of this session")).to_be_visible()
    _check(page, width)
    page.keyboard.press("Tab")
    focus = page.evaluate(
        "() => { const s = getComputedStyle(document.activeElement);"
        " return [s.outlineStyle, parseFloat(s.outlineWidth)]; }"
    )
    assert focus[0] != "none"
    assert focus[1] >= 2
