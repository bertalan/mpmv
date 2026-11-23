"""E2E (THM.21): the `ride` header refinement (spec 14 §14.6).

At 375 px the header is one row of at most 64 px (logo, search, cart, menu button) and the
language switcher is in the menu drawer. At 1280 px the home header overlays the hero, is
transparent over it with a scrim gradient behind the links (axe finds no contrast failure), and
is solid once the page is scrolled. A focused element is never hidden by the header (WCAG 2.4.11).
Other pages keep the solid bar.
"""

from typing import Any

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from tests.e2e.test_ride_home import _ride_home  # noqa: F401 (autouse fixture)
from tests.e2e.test_ride_overlays import clean, open_page
from tests.i18n import translated

pytestmark = pytest.mark.django_db(transaction=True)

BOX = (
    "(sel) => { const r = document.querySelector(sel).getBoundingClientRect();"
    " return [r.top, r.bottom]; }"
)
BACKGROUND = "() => getComputedStyle(document.querySelector('.site-header'))"


def test_the_phone_header_is_one_row_of_at_most_64px(page: Page, live_server: LiveServer) -> None:
    open_page(page, live_server, "/", 375)
    assert page.evaluate("() => document.querySelector('.site-header').offsetHeight") <= 64
    centres = [
        sum(page.evaluate(BOX, selector)) / 2
        for selector in (".site-name", ".tool--cart", ".nav-toggle", ".site-header__tools a")
    ]
    assert max(centres) - min(centres) <= 4, "logo, search, cart and menu sit on one row"
    for selector in (".site-name", ".nav-toggle", ".site-header__tools a", ".tool--cart"):
        top, bottom = page.evaluate(BOX, selector)
        assert 0 <= top <= bottom <= 64
    expect(page.locator(".site-header > * nav.language-switcher")).to_be_hidden()
    assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth")


def test_the_phone_language_switcher_is_in_the_menu_drawer(
    page: Page, live_server: LiveServer
) -> None:
    open_page(page, live_server, "/", 375)
    switcher = page.locator("#site-offcanvas nav.language-switcher")
    expect(switcher).to_have_count(1)
    expect(page.locator("nav.language-switcher")).to_have_count(1)
    page.get_by_role("button", name=translated("Menu")).click()
    expect(switcher).to_be_visible()
    clean(page, include="#site-offcanvas")


def test_the_home_header_overlays_the_hero_and_turns_solid_on_scroll(
    page: Page, live_server: LiveServer
) -> None:
    open_page(page, live_server, "/", 1280)
    header_top, header_bottom = page.evaluate(BOX, ".site-header")
    hero_top, _ = page.evaluate(BOX, ".hero")
    assert header_top == 0, "the header sits at the top"
    assert hero_top <= 1, "the hero starts under the header"
    assert header_bottom > hero_top
    background = page.evaluate(f"({BACKGROUND})().backgroundImage")
    assert "linear-gradient" in background, "a scrim gradient sits behind the links"
    clean(page)
    page.evaluate("document.body.style.minHeight = '3000px'")
    page.evaluate("window.scrollTo(0, 200)")
    page.wait_for_function("() => document.documentElement.classList.contains('is-away')")
    assert page.evaluate(f"({BACKGROUND})().backgroundImage") == "none"
    colour = page.evaluate(f"({BACKGROUND})().backgroundColor")
    assert colour.startswith("rgb("), "solid once scrolled"
    assert not colour.startswith("rgba(")
    clean(page)


def test_a_page_without_a_hero_keeps_the_solid_header(page: Page, live_server: LiveServer) -> None:
    open_page(page, live_server, "/about/", 1280)
    assert page.evaluate(f"({BACKGROUND})().backgroundImage") == "none"


def test_a_focused_link_is_not_hidden_under_the_overlay_header(
    page: Page, live_server: LiveServer
) -> None:
    open_page(page, live_server, "/", 1280)
    page.evaluate("document.body.style.minHeight = '3000px'")
    page.locator(".site-footer a").first.focus()
    page.keyboard.press("Shift+Tab")
    for _ in range(40):
        page.keyboard.press("Shift+Tab")
        top = page.evaluate(
            "() => { const r = document.activeElement.getBoundingClientRect(); return r.top; }"
        )
        header_bottom = page.evaluate(BOX, ".site-header")[1]
        in_header = page.evaluate(
            "() => document.activeElement === document.body"
            " || !!document.activeElement.closest('.site-header, .skip-link')"
        )
        if not in_header:
            assert top >= header_bottom - 1, page.evaluate("document.activeElement.outerHTML")


def test_the_phone_tab_order_is_the_visual_order(page: Page, live_server: LiveServer) -> None:
    """WCAG 2.4.3: logo, search, cart, menu button, left to right and in the Tab order."""
    open_page(page, live_server, "/", 375)
    visual = page.evaluate(
        """() => [
                '.site-name', '.site-header__tools a:nth-of-type(1)', '.tool--cart', '.nav-toggle',
            ]
            .map((sel) => document.querySelector(sel))
            .sort((a, b) => a.getBoundingClientRect().left - b.getBoundingClientRect().left)
            .map((el) => el.className)"""
    )
    assert visual[0] == "site-name"
    page.locator(".site-name").focus()
    tabbed = ["site-name"]
    for _ in range(3):
        page.keyboard.press("Tab")
        tabbed.append(page.evaluate("() => document.activeElement.className"))
    assert tabbed == visual, (tabbed, visual)
    assert "nav-toggle" in tabbed[-1]


def test_without_javascript_the_phone_header_wraps_the_menu_below_the_bar(
    browser: Any, live_server: LiveServer
) -> None:
    for width in (360, 375):
        context = browser.new_context(
            java_script_enabled=False, viewport={"width": width, "height": 800}
        )
        try:
            page = context.new_page()
            assert page.goto(f"{live_server.url}/") is not None
            assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth")
            logo_bottom = page.evaluate(BOX, ".site-name")[1]
            nav_top = page.evaluate(BOX, ".site-nav")[0]
            assert nav_top >= logo_bottom - 1, (width, nav_top, logo_bottom)
        finally:
            context.close()


def test_without_javascript_the_overlay_page_keeps_a_solid_header(
    browser: Any, live_server: LiveServer
) -> None:
    context = browser.new_context(
        java_script_enabled=False, viewport={"width": 1280, "height": 900}
    )
    try:
        page = context.new_page()
        assert page.goto(f"{live_server.url}/") is not None
        assert page.locator(".block-hero").count() == 1
        assert page.evaluate(f"({BACKGROUND})().backgroundImage") == "none"
    finally:
        context.close()
