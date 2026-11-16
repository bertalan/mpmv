"""E2E (THM.15 part 1): the `ride` header as a Bootstrap navbar (spec 14 §14.6).

Dropdown sub-menus and the offcanvas mobile menu (below 64rem) work with the keyboard alone:
the offcanvas opens from its toggler, traps focus, closes with Escape and returns focus to the
toggler; a dropdown opens and closes with the keyboard; axe `wcag22aa` is clean and nothing
raises a `securitypolicyviolation` at 360, 768 and 1280 px.
"""

from types import SimpleNamespace
from typing import Any

import pytest
from playwright.sync_api import Page, expect
from pytest_django.fixtures import Settings as SettingsWrapper
from pytest_django.live_server_helper import LiveServer

from apps.cms.templatetags import cms_tags
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)

MENU = [
    SimpleNamespace(label="Shop", url="/shop/", children=[]),
    SimpleNamespace(
        label="Vendors",
        url="/vendors/",
        children=[
            SimpleNamespace(label="All vendors", url="/vendors/"),
            SimpleNamespace(label="Become a vendor", url="/sell/"),
        ],
    ),
    SimpleNamespace(label="About", url="/about/", children=[]),
]


@pytest.fixture(autouse=True)
def _ride_with_submenu(
    settings: SettingsWrapper, live_server: LiveServer, _cms_site: None, monkeypatch: Any
) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    settings.MPMV_HELP_URL = "/about/#help"
    monkeypatch.setattr(cms_tags, "_menu_links", lambda request, menu: list(MENU))


def open_home(page: Page, live_server: LiveServer, width: int) -> None:
    page.emulate_media(reduced_motion="reduce")
    page.set_viewport_size({"width": width, "height": 800})
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    response = page.goto(f"{live_server.url}/")
    assert response is not None
    assert response.status == 200
    page.wait_for_load_state("load")


@pytest.mark.parametrize("width", [360, 768, 1280])
def test_axe_is_clean_and_csp_quiet_on_the_ride_navbar(
    width: int, page: Page, live_server: LiveServer
) -> None:
    open_home(page, live_server, width)
    expect(page.locator("html")).to_have_attribute("data-theme", "ride")
    violations = run_axe(page)
    assert violations == [], format_violations(violations)
    assert csp_violations(page) == [], "\n".join(csp_violations(page))


def test_the_offcanvas_menu_traps_focus_and_returns_it_on_escape(
    page: Page, live_server: LiveServer
) -> None:
    open_home(page, live_server, 360)
    toggle = page.locator(".nav-toggle")
    panel = page.locator("#site-offcanvas")
    expect(panel).to_be_hidden()
    toggle.focus()
    page.keyboard.press("Enter")
    expect(panel).to_be_visible()
    violations = run_axe(page)
    assert violations == [], format_violations(violations)
    for _ in range(12):  # more tabs than the panel has stops: focus never leaves it
        page.keyboard.press("Tab")
        assert page.evaluate(
            "() => document.querySelector('#site-offcanvas').contains(document.activeElement)"
        )
    page.keyboard.press("Escape")
    expect(panel).to_be_hidden()
    expect(toggle).to_be_focused()
    assert csp_violations(page) == [], "\n".join(csp_violations(page))


def test_a_dropdown_opens_and_closes_with_the_keyboard(page: Page, live_server: LiveServer) -> None:
    open_home(page, live_server, 1280)
    toggle = page.locator("[data-bs-toggle='dropdown']")
    menu = page.locator("#subnav-2")
    expect(toggle).to_have_attribute("aria-expanded", "false")
    toggle.focus()
    page.keyboard.press("Enter")
    expect(toggle).to_have_attribute("aria-expanded", "true")
    expect(menu).to_be_visible()
    page.keyboard.press("ArrowDown")
    expect(menu.get_by_role("link").first).to_be_focused()
    page.keyboard.press("Escape")
    expect(menu).to_be_hidden()
    expect(toggle).to_have_attribute("aria-expanded", "false")
    expect(toggle).to_be_focused()
    assert csp_violations(page) == [], "\n".join(csp_violations(page))


def test_a_focused_footer_link_is_not_covered_by_the_sticky_header(
    page: Page, live_server: LiveServer
) -> None:
    open_home(page, live_server, 1280)
    page.locator(".site-footer a").last.focus()
    covered = page.evaluate(
        "() => { const e = document.activeElement.getBoundingClientRect();"
        " const h = document.querySelector('.site-header').getBoundingClientRect();"
        " return e.top < h.bottom; }"
    )
    assert not covered
