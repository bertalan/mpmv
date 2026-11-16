"""E2E (THM.15 part 3): `ride` footer, back-to-top and the sticky header (spec 14 §14.6).

The back-to-top link works with the keyboard alone and without JavaScript; smooth scrolling is
off under reduced motion. A focused element is never hidden by the sticky header (WCAG 2.4.11),
also when shift-tabbing upwards from the footer. axe `wcag22aa` is clean and no
`securitypolicyviolation` is raised on the home page, a listing and a product page at 360, 768
and 1280 px; the help link is in the same place on every page (3.2.6).
"""

from typing import Any

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.catalog.models import Product
from tests.e2e.csp import csp_violations
from tests.e2e.test_add_to_cart import product  # noqa: F401 (fixture)
from tests.e2e.test_catalog_listings import term_page  # noqa: F401 (fixture)
from tests.e2e.test_ride_overlays import _ride, clean, open_page  # noqa: F401 (fixtures)

pytestmark = pytest.mark.django_db(transaction=True)

BACK_TO_TOP = ".site-footer .back-to-top"


def test_back_to_top_is_reached_with_the_keyboard_and_scrolls_to_the_top(
    page: Page, live_server: LiveServer
) -> None:
    open_page(page, live_server, "/", 1280)
    link = page.locator(BACK_TO_TOP)
    expect(link).to_have_attribute("href", "#top")
    page.evaluate("document.body.style.minHeight = '3000px'")
    link.focus()
    expect(link).to_be_visible()
    page.keyboard.press("Enter")
    page.wait_for_function("window.scrollY === 0")
    assert page.url.endswith("#top")
    assert csp_violations(page) == []


def test_smooth_scroll_is_off_under_reduced_motion_and_on_otherwise(
    page: Page, live_server: LiveServer
) -> None:
    open_page(page, live_server, "/", 1280)  # reduced motion is emulated
    assert page.evaluate("getComputedStyle(document.documentElement).scrollBehavior") == "auto"
    page.emulate_media(reduced_motion="no-preference")
    assert page.evaluate("getComputedStyle(document.documentElement).scrollBehavior") == "smooth"


def test_back_to_top_works_without_javascript(browser: Any, live_server: LiveServer) -> None:
    context = browser.new_context(java_script_enabled=False, viewport={"width": 360, "height": 640})
    try:
        page = context.new_page()
        page.goto(f"{live_server.url}/")
        link = page.locator(BACK_TO_TOP)
        expect(link).to_be_visible()
        link.click()
        assert page.url.endswith("#top")
    finally:
        context.close()


@pytest.mark.parametrize("width", [360, 768, 1280])
def test_shift_tab_up_from_the_footer_never_lands_under_the_sticky_header(
    width: int, page: Page, live_server: LiveServer
) -> None:
    open_page(page, live_server, "/", width)
    page.evaluate("document.body.style.minHeight = '3000px'")
    page.locator(BACK_TO_TOP).focus()
    for _ in range(40):
        page.keyboard.press("Shift+Tab")
        hidden = page.evaluate(
            "() => { const e = document.activeElement;"
            " if (e === document.body || e.classList.contains('skip-link')) return false;"
            " const h = document.querySelector('.site-header');"
            " if (getComputedStyle(h).position !== 'sticky') return false;"
            " const r = e.getBoundingClientRect();"
            " if (r.width === 0 || r.height === 0) return false;"
            " const hb = h.getBoundingClientRect().bottom - 1;"
            " const bad = r.top < hb && r.bottom > 0 && !h.contains(e);"
            " return bad ? e.outerHTML.slice(0, 120) : false; }"
        )
        assert not hidden, hidden


@pytest.mark.parametrize("width", [360, 768, 1280])
@pytest.mark.parametrize("where", ["home", "listing", "product"])
def test_the_gates_pass_with_the_footer_chrome_at_every_width(
    where: str,
    width: int,
    page: Page,
    live_server: LiveServer,
    term_page: None,  # noqa: F811
    product: Product,  # noqa: F811
) -> None:
    path = {"home": "/", "listing": "/compatibility/maker/", "product": "/product/blue-vase/"}
    open_page(page, live_server, path[where], width)
    expect(page.locator(BACK_TO_TOP)).to_be_attached()
    expect(
        page.locator(".site-footer .help-link, .site-footer [href*='help']").first
    ).to_be_attached()
    # The listing's own body (filters, pagination) is THM.7's: only the shell is judged there.
    if where == "listing":
        clean(page, include=".site-footer")
    else:
        clean(page)
    assert csp_violations(page) == [], "\n".join(csp_violations(page))
