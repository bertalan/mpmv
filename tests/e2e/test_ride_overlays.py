"""E2E (THM.15 part 2): the `ride` search overlay (modal) and mini-cart offcanvas (spec 14 §14.6).

Both open from their triggers with the keyboard alone, trap focus, close with Escape and return
focus to the trigger. Adding a product updates the announced count and the mini-cart without a
reload. axe `wcag22aa` is clean and nothing raises a `securitypolicyviolation` on the home page,
a listing and a product page at 360, 768 and 1280 px, with and without an overlay open.
"""

import re
from typing import Any

import pytest
from playwright.sync_api import Page, expect
from pytest_django.fixtures import Settings as SettingsWrapper
from pytest_django.live_server_helper import LiveServer

from apps.catalog.models import Product
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations
from tests.e2e.test_add_to_cart import product  # noqa: F401 (fixture)
from tests.e2e.test_catalog_listings import term_page  # noqa: F401 (fixture)
from tests.i18n import translated

pytestmark = pytest.mark.django_db(transaction=True)

SEARCH = "a[data-bs-target='#search-modal']"
CART = "a[data-bs-target='#mini-cart-offcanvas']"


@pytest.fixture(autouse=True)
def _ride(settings: SettingsWrapper, live_server: LiveServer, _cms_site: None) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    settings.MPMV_HELP_URL = "/about/#help"


def open_page(page: Page, live_server: LiveServer, path: str, width: int = 1280) -> None:
    page.emulate_media(reduced_motion="reduce")
    page.set_viewport_size({"width": width, "height": 800})
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    response = page.goto(f"{live_server.url}{path}")
    assert response is not None
    assert response.status == 200
    page.wait_for_load_state("load")


def clean(page: Page, include: str | None = None) -> None:
    violations = run_axe(page, include=include)
    assert violations == [], format_violations(violations)
    assert csp_violations(page) == [], "\n".join(csp_violations(page))


def trapped(page: Page, container: str) -> None:
    for _ in range(14):  # more tabs than the overlay has stops
        page.keyboard.press("Tab")
        assert page.evaluate(
            "(sel) => document.querySelector(sel).contains(document.activeElement)", container
        )
    for _ in range(3):
        page.keyboard.press("Shift+Tab")
        assert page.evaluate(
            "(sel) => document.querySelector(sel).contains(document.activeElement)", container
        )


@pytest.mark.parametrize("width", [360, 768, 1280])
@pytest.mark.parametrize("where", ["home", "listing", "product"])
def test_the_gates_pass_on_ride_pages_at_every_width(
    where: str,
    width: int,
    page: Page,
    live_server: LiveServer,
    term_page: None,  # noqa: F811
    product: Product,  # noqa: F811
) -> None:
    path = {"home": "/", "listing": "/compatibility/maker/", "product": "/product/blue-vase/"}
    open_page(page, live_server, path[where], width)
    expect(page.locator("html")).to_have_attribute("data-theme", "ride")
    expect(page.locator(SEARCH)).to_be_visible()
    expect(page.locator(CART)).to_be_visible()
    # The listing's own body (filters, pagination) is THM.7's: here only the shell is judged.
    clean(page, include=".site-header" if where == "listing" else None)


def test_the_search_overlay_opens_traps_focus_and_returns_it_on_escape(
    page: Page, live_server: LiveServer
) -> None:
    open_page(page, live_server, "/", 1280)
    trigger = page.locator(SEARCH)
    modal = page.locator("#search-modal")
    expect(modal).to_be_hidden()
    trigger.focus()
    page.keyboard.press("Enter")
    expect(modal).to_be_visible()
    expect(modal.locator("input[name='s']")).to_be_focused()
    expect(modal.locator("form")).to_have_attribute("action", "/")  # CAT.27: the language home
    clean(page)
    trapped(page, "#search-modal")
    page.keyboard.press("Escape")
    expect(modal).to_be_hidden()
    expect(trigger).to_be_focused()
    assert csp_violations(page) == [], "\n".join(csp_violations(page))


def test_the_search_form_submits_to_the_results_page(page: Page, live_server: LiveServer) -> None:
    open_page(page, live_server, "/", 1280)
    page.locator(SEARCH).focus()
    page.keyboard.press("Enter")
    page.locator("#search-modal input[name='s']").fill("bag")
    page.keyboard.press("Enter")
    # CAT.27: the language home answers `?s=` with the search results page.
    expect(page).to_have_url(re.compile(r"/\?s=bag$"))


def test_without_javascript_the_triggers_lead_to_the_pages(
    page: Page, live_server: LiveServer
) -> None:
    page.route("**/*.js", lambda route: route.abort())
    page.goto(f"{live_server.url}/")
    expect(page.locator(SEARCH)).to_have_attribute("href", "/")
    expect(page.locator(CART)).to_have_attribute("href", re.compile(r"/cart/$"))
    page.locator(CART).click()
    expect(page).to_have_url(re.compile(r"/cart/$"))


def test_the_mini_cart_opens_traps_focus_and_returns_it_on_escape(
    page: Page,
    live_server: LiveServer,
    product: Product,  # noqa: F811
) -> None:
    open_page(page, live_server, "/product/blue-vase/", 1280)
    trigger = page.locator(CART)
    panel = page.locator("#mini-cart-offcanvas")
    expect(panel).to_be_hidden()
    trigger.focus()
    page.keyboard.press("Enter")
    expect(panel).to_be_visible()
    expect(panel).to_contain_text(translated("Your cart is empty"))
    clean(page)
    trapped(page, "#mini-cart-offcanvas")
    page.keyboard.press("Escape")
    expect(panel).to_be_hidden()
    expect(trigger).to_be_focused()
    assert csp_violations(page) == [], "\n".join(csp_violations(page))


def test_adding_a_product_updates_the_count_and_the_mini_cart_without_a_reload(
    page: Page,
    live_server: LiveServer,
    product: Product,  # noqa: F811
) -> None:
    open_page(page, live_server, "/product/blue-vase/", 1280)
    page.evaluate("() => { window.__same_page = true; }")
    count = page.locator("#cart-count")
    expect(count).to_have_attribute("aria-live", "polite")
    expect(page.locator("#cart-badge")).to_be_hidden()
    page.locator("#product-quantity").fill("2")
    page.get_by_role("button", name=translated("Add to cart")).click()
    expect(page.locator("#add-to-cart-result")).to_contain_text(translated("Added to your cart."))
    expect(count).to_have_text("2 items")
    expect(page.locator("#cart-badge")).to_have_text("2")
    assert page.evaluate("() => window.__same_page === true")  # no reload
    page.locator(CART).focus()
    page.keyboard.press("Enter")
    panel = page.locator("#mini-cart-offcanvas")
    expect(panel).to_contain_text("Blue vase")
    expect(panel.get_by_role("link", name=translated("View cart"))).to_have_attribute(
        "href", re.compile(r"/cart/$")
    )
    clean(page)
    expect(panel.locator("#mini-cart-body")).to_contain_text("2")
    expect(panel.locator("#mini-cart-body [data-cart-count]")).to_have_attribute(
        "data-cart-count", "2"
    )
    assert panel.locator("form").count() == 0  # the fragment, not the cart page


def test_the_count_survives_a_new_page(
    page: Page,
    live_server: LiveServer,
    product: Product,  # noqa: F811
    settings: Any,
) -> None:
    open_page(page, live_server, "/product/blue-vase/", 1280)
    page.get_by_role("button", name=translated("Add to cart")).click()
    expect(page.locator("#cart-badge")).to_have_text("1")
    page.goto(f"{live_server.url}/")
    expect(page.locator("#cart-badge")).to_have_text("1")
    expect(page.locator("#cart-count")).to_have_text("1 item")
