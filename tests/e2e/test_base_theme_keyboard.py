"""E2E (THM.3): keyboard-only use of the `base` theme (spec 14 §14.6, WCAG 2.2 AA).

Done when: "keyboard-only e2e reaches every control with a visible focus". On the home page, a
standard page and the sign-in (MFA verification) page, the Tab key alone reaches every visible
control, each one shows a focus outline of at least 2 px, the skip link comes first and moves
focus to the main content, header controls are at least 24 x 24 px, and the narrow-screen menu
is a disclosure button that works with Enter and Escape and needs no JavaScript to show the
links. No page raises a CSP violation.
"""

import re
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, Page, expect
from pytest_django.fixtures import Settings as SettingsWrapper
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations
from tests.e2e.login import admin_login

pytestmark = pytest.mark.django_db(transaction=True)

# Every control a keyboard user should reach: visible and not disabled.
MARK_CONTROLS = """
() => {
  const selector = 'a[href], button:not([disabled]), input:not([type=hidden]):not([disabled]),'
    + ' select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
  const controls = [...document.querySelectorAll(selector)].filter((el) =>
    !el.closest('[hidden]') && el.getClientRects().length > 0
    && getComputedStyle(el).visibility !== 'hidden');
  controls.forEach((el, index) => el.setAttribute('data-kbd', String(index)));
  return controls.length;
}
"""

FOCUSED = """
() => {
  const el = document.activeElement;
  if (!el || el === document.body) return null;
  const style = getComputedStyle(el);
  const box = el.getBoundingClientRect();
  return {
    kbd: el.getAttribute('data-kbd'),
    label: (el.getAttribute('aria-label') || el.textContent || el.name || el.id || '')
      .trim().slice(0, 40),
    outlineStyle: style.outlineStyle,
    outlineWidth: parseFloat(style.outlineWidth),
    width: box.width,
    height: box.height,
    inHeader: !!el.closest('header, footer'),
    inViewport: box.bottom > 0 && box.top < window.innerHeight,
  };
}
"""


@pytest.fixture(autouse=True)
def _help_url(settings: SettingsWrapper) -> None:
    settings.MPMV_HELP_URL = "/about/#help"


def tab_through(page: Page) -> list[dict[str, Any]]:
    """Press Tab until every control was reached once (or twice the count): the focus stops."""
    # After a click-driven navigation the URL can match before theme.css has loaded.
    page.wait_for_load_state("load")
    total = page.evaluate(MARK_CONTROLS)
    assert total > 0
    stops: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _ in range(total * 2 + 4):
        page.keyboard.press("Tab")
        focused = page.evaluate(FOCUSED)
        if focused is None or focused["kbd"] is None:
            continue
        stops.append(focused)
        seen.add(focused["kbd"])
        if len(seen) == total:
            break
    missing = sorted(set(map(str, range(total))) - seen, key=int)
    unreached = page.evaluate(
        "(ids) => ids.map((i) => document.querySelector(`[data-kbd='${i}']`).outerHTML"
        ".slice(0, 120))",
        missing,
    )
    assert not missing, f"not reachable with Tab: {unreached}"
    return stops


def assert_visible_focus(stops: list[dict[str, Any]]) -> None:
    for stop in stops:
        assert stop["outlineStyle"] != "none", f"no focus outline on {stop['label']!r}"
        assert stop["outlineWidth"] >= 2, f"focus outline under 2 px on {stop['label']!r}"
        assert stop["inViewport"], f"focused {stop['label']!r} is off screen"


def assert_target_size(stops: list[dict[str, Any]]) -> None:
    for stop in stops:
        if stop["inHeader"]:
            size = f"{stop['label']!r} is {stop['width']}x{stop['height']} px"
            assert stop["width"] >= 24, size
            assert stop["height"] >= 24, size


@pytest.mark.parametrize("path", ["/", "/about/"])
def test_keyboard_reaches_every_control_with_a_visible_focus(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path], path: str
) -> None:
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    page.goto(f"{live_server.url}{path}")
    expect(page.locator("html")).to_have_attribute("data-theme", "base")

    page.keyboard.press("Tab")
    skip = page.locator(".skip-link")
    expect(skip).to_be_focused()
    expect(skip).to_be_in_viewport()
    shot("skip-link")
    page.keyboard.press("Enter")
    expect(page.locator("main#main")).to_be_focused()

    page.goto(f"{live_server.url}{path}")
    stops = tab_through(page)
    assert stops[0]["label"] == skip.inner_text().strip()
    assert_visible_focus(stops)
    assert_target_size(stops)
    shot("keyboard")
    assert csp_violations(page) == [], "\n".join(csp_violations(page))


def test_keyboard_reaches_every_control_on_the_sign_in_page(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    user = UserFactory.create(email="staff@example.com", password=password, staff=True)
    TOTPDeviceFactory.create(user=user)
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    admin_login(page, live_server.url, "staff@example.com", password)
    expect(page).to_have_url(re.compile(r"/account/mfa/verify/"))
    expect(page.locator("html")).to_have_attribute("data-theme", "base")

    stops = tab_through(page)
    assert_visible_focus(stops)
    assert_target_size(stops)
    shot("sign-in")
    assert csp_violations(page) == [], "\n".join(csp_violations(page))


def test_the_narrow_menu_is_a_keyboard_disclosure(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    page.set_viewport_size({"width": 375, "height": 800})
    page.goto(f"{live_server.url}/")
    menu = page.locator("button[aria-controls='site-nav']")
    about = page.locator("#site-nav").get_by_role("link", name="About us")
    expect(menu).to_be_visible()
    expect(menu).to_have_attribute("aria-expanded", "false")
    expect(about).to_be_hidden()
    shot("closed")

    for _ in range(10):
        page.keyboard.press("Tab")
        if menu.evaluate("(el) => el === document.activeElement"):
            break
    expect(menu).to_be_focused()
    page.keyboard.press("Enter")
    expect(menu).to_have_attribute("aria-expanded", "true")
    expect(about).to_be_visible()
    shot("open")
    page.keyboard.press("Tab")
    expect(page.locator("#site-nav a").first).to_be_focused()

    page.keyboard.press("Escape")
    expect(menu).to_have_attribute("aria-expanded", "false")
    expect(menu).to_be_focused()
    expect(about).to_be_hidden()
    assert csp_violations(page) == [], "\n".join(csp_violations(page))


def test_without_javascript_the_navigation_links_are_shown(
    browser: Browser, live_server: LiveServer
) -> None:
    context = browser.new_context(java_script_enabled=False, viewport={"width": 375, "height": 800})
    page = context.new_page()
    page.goto(f"{live_server.url}/")
    expect(page.locator("button[aria-controls='site-nav']")).to_be_hidden()
    expect(page.locator("#site-nav").get_by_role("link", name="About us")).to_be_visible()
    context.close()


def test_reduced_motion_is_respected(page: Page, live_server: LiveServer) -> None:
    page.emulate_media(reduced_motion="reduce")
    page.goto(f"{live_server.url}/")
    durations = page.evaluate(
        "() => [...document.querySelectorAll('a, button')].map("
        "(el) => getComputedStyle(el).transitionDuration)"
    )
    assert all(d in ("0s", "") for d in durations), durations
