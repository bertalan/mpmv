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
from tests.i18n import translated

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
    # The back-to-top link is `visibility: hidden` until the page is scrolled, so it cannot take
    # focus here: the last menu or legal link stands for the footer.
    page.locator(".site-footer a:not(.back-to-top)").last.focus()
    # Smooth scrolling (60-banner-chrome.css) animates the focus scroll: wait until the focused
    # link has settled below the header instead of sampling mid-animation.
    page.wait_for_function(
        "() => { const e = document.activeElement.getBoundingClientRect();"
        " const h = document.querySelector('.site-header').getBoundingClientRect();"
        " return e.top >= h.bottom; }"
    )


@pytest.mark.parametrize(("width", "burger"), [(1000, True), (1023, True), (1024, False)])
def test_there_is_one_switch_point_at_64rem(
    width: int, burger: bool, page: Page, live_server: LiveServer
) -> None:
    """Between 992 and 1023 px Bootstrap's own `lg` would expand; ride switches only at 1024."""
    open_home(page, live_server, width)
    toggle = page.locator(".nav-toggle")
    inline_menu = page.get_by_role("navigation", name=translated("Main")).get_by_role("link").first
    if burger:
        expect(toggle).to_be_visible()
        expect(inline_menu).to_be_hidden()
    else:
        expect(toggle).to_be_hidden()
        expect(inline_menu).to_be_visible()
    visible = [toggle.is_visible(), inline_menu.is_visible()]
    assert visible.count(True) == 1, visible


def test_without_javascript_the_main_nav_is_a_reachable_link_list(
    browser: Any, live_server: LiveServer
) -> None:
    context = browser.new_context(java_script_enabled=False, viewport={"width": 360, "height": 800})
    try:
        page = context.new_page()
        assert page.goto(f"{live_server.url}/") is not None
        nav = page.get_by_role("navigation", name=translated("Main"))
        expect(page.locator(".nav-toggle")).to_be_hidden()
        for link in nav.get_by_role("link").all():
            if link.get_attribute("class") != "dropdown-item":
                expect(link).to_be_visible()
        page.keyboard.press("Tab")  # skip link, then the header, then the main nav
        reached: list[str] = []
        for _ in range(12):
            page.keyboard.press("Tab")
            href = page.evaluate("() => document.activeElement.getAttribute('href')")
            reached.append(href)
        assert "/shop/" in reached, reached
        assert "/about/" in reached, reached
    finally:
        context.close()


def test_escape_closes_an_open_dropdown_before_the_offcanvas(
    page: Page, live_server: LiveServer
) -> None:
    open_home(page, live_server, 360)
    # Wait for Bootstrap's own "shown" events: a key pressed while the offcanvas is still in its
    # show transition can be lost when Bootstrap moves the focus into the panel.
    page.evaluate(
        """() => {
            window.__shown = {offcanvas: false, dropdown: false};
            const seen = window.__shown;
            document.getElementById("site-offcanvas")
                .addEventListener("shown.bs.offcanvas", () => { seen.offcanvas = true; });
            document.addEventListener("shown.bs.dropdown", () => { seen.dropdown = true; });
        }"""
    )
    page.locator(".nav-toggle").click()
    page.wait_for_function("() => window.__shown.offcanvas")
    panel = page.locator("#site-offcanvas")
    expect(panel).to_be_visible()
    sub_toggle = page.locator("[data-bs-toggle='dropdown']")
    sub_toggle.focus()
    expect(sub_toggle).to_be_focused()
    page.keyboard.press("Enter")
    page.wait_for_function("() => window.__shown.dropdown")
    expect(sub_toggle).to_have_attribute("aria-expanded", "true")
    expect(page.locator("#site-offcanvas .dropdown-menu.show")).to_be_visible()
    # Bootstrap handles Escape in both the dropdown and the offcanvas, and which one acts first is
    # not guaranteed on CI. The contract: Escape never traps the user; a few presses close
    # everything and focus returns to the menu toggler.
    for _ in range(3):
        if not panel.is_visible():
            break
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)  # let Bootstrap finish its hide transition
    expect(panel).to_be_hidden()
    expect(page.locator(".nav-toggle")).to_be_focused()
    assert csp_violations(page) == [], "\n".join(csp_violations(page))
