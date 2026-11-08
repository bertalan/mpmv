"""E2E (THM.4, #119 review): a wrapped sticky header never hides the focused element.

From 48rem up the header is sticky. With a long header menu it wraps to a second row at about
800 px, taller than the `--header-height` token. The scroll padding must follow the header's
real height (WCAG 2.4.11): a control scrolled just under the top edge, where the browser
considers it in view and does not scroll on focus, must stay visible when Shift+Tab reaches it.
"""

from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer

from apps.cms.models import NavigationMenu
from tests.e2e.test_base_theme_focus import (
    ADD_SPACER,
    FOCUS_VISIBLE_AT_CENTRE,
    SCROLL_TO_TOP_EDGE,
    focus_kbd,
)
from tests.e2e.test_base_theme_keyboard import MARK_CONTROLS

pytestmark = pytest.mark.django_db(transaction=True)

EXTRA_LINKS = ("Shipping and returns", "Become a vendor", "Contact customer service")

HEADER_HEIGHT = "() => document.querySelector('.site-header').getBoundingClientRect().height"
SCROLL_PADDING = "() => parseFloat(getComputedStyle(document.documentElement).scrollPaddingTop)"


def long_header_menu(base_url: str) -> None:
    """Four links in the primary language's header menu: the seeded About page and three more."""
    menu = NavigationMenu.objects.get(
        location=NavigationMenu.Location.HEADER, locale__language_code="en"
    )
    links = [(block.block_type, block.value) for block in menu.links]
    links += [
        ("external", {"url": f"{base_url}/about/#{n}", "label": label})
        for n, label in enumerate(EXTRA_LINKS)
    ]
    menu.links = links
    menu.save()


def test_shift_tab_under_a_wrapped_sticky_header_at_800px(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    long_header_menu(live_server.url)
    page.set_viewport_size({"width": 800, "height": 640})
    page.goto(f"{live_server.url}/no-such-page/")
    page.wait_for_load_state("load")
    assert page.locator(".site-nav li").count() == 4
    header = page.evaluate(HEADER_HEIGHT)
    assert header > 72, f"the header should wrap at 800 px, it is {header} px high"
    # base.js measures the header once it is laid out.
    page.wait_for_function(
        f"() => Math.abs(({SCROLL_PADDING})() - ({HEADER_HEIGHT})()) < 1", timeout=5000
    )
    assert (
        page.evaluate("() => getComputedStyle(document.querySelector('.site-header')).position")
        == "sticky"
    )

    page.evaluate(ADD_SPACER)
    total: int = page.evaluate(MARK_CONTROLS)
    home = page.locator(".error-page a")
    target = home.get_attribute("data-kbd")
    assert target is not None
    focus_kbd(page, str(int(target) + 1), total + 2)
    home.evaluate(SCROLL_TO_TOP_EDGE)
    assert page.evaluate("() => window.scrollY") > 0
    page.keyboard.press("Shift+Tab")
    stop = page.evaluate(FOCUS_VISIBLE_AT_CENTRE)
    shot("wrapped-header")
    assert stop is not None
    assert stop["kbd"] == target
    assert stop["visible"], stop
