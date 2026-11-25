"""E2E (THM.27 1/2): the `ride` cart page is two columns on desktop and stacked on a phone.

Axe (WCAG 2.2 AA) and the CSP stay quiet at 375 and 1280 px, the summary is reached by keyboard
after the packages, and the page does not scroll sideways. Visual baselines: THM.10.
"""

import secrets
from typing import Any

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.cart.models import Cart
from tests.e2e.test_checkout import check_page as _check_page
from tests.e2e.test_checkout import open_with_cart, shop  # noqa: F401 (fixture)
from tests.e2e.test_ride_checkout import tab_to


@pytest.fixture(autouse=True)
def _ride(settings: Any) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    settings.STRIPE_PUBLISHABLE_KEY = "pk_" + secrets.token_urlsafe(12)


@pytest.mark.parametrize("width", [375, 1280])
def test_the_cart_layout_passes_the_gates_and_follows_the_width(
    page: Page,
    live_server: LiveServer,
    shop: Cart,  # noqa: F811
    width: int,
) -> None:
    page.set_viewport_size({"width": width, "height": 900})
    open_with_cart(page, live_server, shop)
    page.goto(f"{live_server.url}/cart/")
    page.wait_for_load_state("load")
    _check_page(page)
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")

    packages = page.locator(".cart-layout > div").first.bounding_box()
    summary = page.locator(".cart-summary").bounding_box()
    assert packages is not None
    assert summary is not None
    if width >= 1000:
        assert summary["x"] >= packages["x"] + packages["width"] - 1, "summary on the right"
        assert abs(summary["y"] - packages["y"]) < 40, "side by side"
    else:
        assert summary["y"] >= packages["y"] + packages["height"] - 1, "summary below"
        assert summary["width"] >= packages["width"] - 1, "stacked at full width"


def test_the_continue_link_is_reached_by_keyboard_after_the_packages(
    page: Page,
    live_server: LiveServer,
    shop: Cart,  # noqa: F811
) -> None:
    page.set_viewport_size({"width": 1280, "height": 900})
    open_with_cart(page, live_server, shop)
    page.goto(f"{live_server.url}/cart/")
    update = page.locator(".cart-layout form button[type=submit]").last
    tab_to(page, update)
    page.keyboard.press("Tab")
    expect(page.locator(".cart-summary a.btn-primary")).to_be_focused()
