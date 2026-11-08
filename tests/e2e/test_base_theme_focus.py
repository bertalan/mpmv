"""E2E (THM.3): focus not obscured by the sticky header (WCAG 2.4.11, spec 14 §14.6).

On a 375 px wide screen the header wraps to two rows or more (90 px and up), taller than the
4rem scroll padding. When a control sits just under the top edge of the viewport, inside the
scroll padding's reach but under the header, the browser does not scroll on focus: moving the
focus there with Shift+Tab must still leave the control visible. The element at the centre of
its box must be the control itself, never the header.

The 404 page is made long with a spacer after its home link (added through the CSSOM, which the
CSP allows), so the page scrolls and the link can sit at the top edge.
"""

from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer

from tests.e2e.test_base_theme_keyboard import MARK_CONTROLS

pytestmark = pytest.mark.django_db(transaction=True)

# The focused control, and whether the element painted at the centre of its box is that control
# (or one of its children): false when the sticky header covers it or it is off screen.
FOCUS_VISIBLE_AT_CENTRE = """
() => {
  const el = document.activeElement;
  if (!el || el === document.body) return null;
  const box = el.getBoundingClientRect();
  const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
  return {
    kbd: el.getAttribute('data-kbd'),
    label: (el.textContent || el.getAttribute('aria-label') || '').trim().slice(0, 40),
    top: box.top,
    visible: !!hit && (hit === el || el.contains(hit)),
    covered_by: hit ? hit.outerHTML.slice(0, 80) : null,
  };
}
"""

ADD_SPACER = """
() => {
  const spacer = document.createElement('div');
  spacer.style.height = '1500px';
  document.querySelector('.error-page').append(spacer);
}
"""

# Scroll so the top of `el` sits 8 px below the scroll padding: the browser treats it as in view.
SCROLL_TO_TOP_EDGE = """
(el) => {
  const padding = parseFloat(getComputedStyle(document.documentElement).scrollPaddingTop) || 0;
  window.scrollBy(0, el.getBoundingClientRect().top - padding - 8);
}
"""


def open_long_404(page: Page, live_server: LiveServer) -> int:
    page.set_viewport_size({"width": 375, "height": 640})
    page.goto(f"{live_server.url}/no-such-page/")
    page.wait_for_load_state("load")
    page.evaluate(ADD_SPACER)
    total: int = page.evaluate(MARK_CONTROLS)
    return total


def focus_kbd(page: Page, kbd: str, presses: int) -> None:
    for _ in range(presses):
        page.keyboard.press("Tab")
        if page.evaluate("() => document.activeElement.getAttribute('data-kbd')") == kbd:
            return
    raise AssertionError(f"control {kbd} not reached with Tab")


def test_shift_tab_to_a_control_at_the_top_edge_leaves_it_visible_at_375px(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    total = open_long_404(page, live_server)
    home = page.locator(".error-page a")
    target = home.get_attribute("data-kbd")
    assert target is not None
    # Focus the control after the home link, scroll the link to the top edge, come back.
    focus_kbd(page, str(int(target) + 1), total + 2)
    home.evaluate(SCROLL_TO_TOP_EDGE)
    assert page.evaluate("() => window.scrollY") > 0
    page.keyboard.press("Shift+Tab")
    stop = page.evaluate(FOCUS_VISIBLE_AT_CENTRE)
    shot("top-edge")
    assert stop is not None
    assert stop["kbd"] == target
    assert stop["visible"], stop


def test_shift_tab_from_the_bottom_never_hides_the_focus_at_375px(
    page: Page, live_server: LiveServer
) -> None:
    total = open_long_404(page, live_server)
    focus_kbd(page, str(total - 1), total + 2)
    assert page.evaluate("() => window.scrollY") > 0, "the page should scroll at this size"

    stops = []
    for _ in range(total - 1):
        page.keyboard.press("Shift+Tab")
        stop = page.evaluate(FOCUS_VISIBLE_AT_CENTRE)
        if stop is not None:
            stops.append(stop)
    assert any(stop["label"] == "Go to the home page" for stop in stops)
    hidden = [stop for stop in stops if not stop["visible"]]
    assert hidden == [], hidden
