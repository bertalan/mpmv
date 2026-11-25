"""E2E (THM.27 2/2): the `ride` review step is two columns with the summary beside the packages,
shipping options are tiles over native radios, and the sign-in is a centred card with the
show-password toggle inside the field. Axe (WCAG 2.2 AA) and the CSP stay quiet at 375 and
1280 px. Visual baselines: THM.10 (`checkout`, `sign-in`)."""

import secrets
from typing import Any

import pytest
from django.conf import settings as django_settings
from django.utils import translation
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.cart.models import Cart
from tests.e2e.csp import RECORD_CSP_VIOLATIONS
from tests.e2e.test_checkout import check_page as _check_page
from tests.e2e.test_checkout import fill_delivery, open_with_cart, shop  # noqa: F401 (fixture)
from tests.e2e.test_ride_checkout import tab_to
from tests.i18n import translated

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def _ride(settings: Any) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    settings.STRIPE_PUBLISHABLE_KEY = "pk_" + secrets.token_urlsafe(12)


def _to_review(page: Page, live_server: LiveServer, shop: Cart) -> None:  # noqa: F811
    open_with_cart(page, live_server, shop)
    page.goto(f"{live_server.url}/checkout/")
    fill_delivery(page)
    page.locator("#id_city").press("Enter")
    expect(page.get_by_role("heading", level=1)).to_have_text(translated("Review and pay"))


@pytest.mark.parametrize("width", [375, 1280])
def test_the_review_step_is_two_columns_on_desktop_and_stacked_on_a_phone(
    page: Page,
    live_server: LiveServer,
    shop: Cart,  # noqa: F811
    width: int,
) -> None:
    page.set_viewport_size({"width": width, "height": 900})
    _to_review(page, live_server, shop)
    _check_page(page)
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
    body = page.locator(".checkout-layout > div").first.bounding_box()
    summary = page.locator(".cart-summary").bounding_box()
    actions = page.locator("#review-actions").bounding_box()
    assert body is not None
    assert summary is not None
    assert actions is not None
    if width >= 1000:
        assert summary["x"] >= body["x"] + body["width"] - 1, "summary on the right"
    else:
        assert summary["y"] >= body["y"] + body["height"] - 1, "summary below"
    assert actions["y"] >= summary["y"] + summary["height"] - 1, "actions follow the row"


def test_the_shipping_tile_shows_focus_and_the_checked_state(
    page: Page,
    live_server: LiveServer,
    shop: Cart,  # noqa: F811
) -> None:
    page.set_viewport_size({"width": 1280, "height": 900})
    _to_review(page, live_server, shop)
    radio = page.get_by_role("radio").first
    tab_to(page, radio)
    tile = page.locator(".shipping-tile").first
    assert tile.evaluate("el => getComputedStyle(el).outlineStyle") == "solid", "visible focus"
    assert radio.is_checked()
    assert tile.evaluate("el => parseFloat(getComputedStyle(el).borderTopWidth)") >= 1
    page.keyboard.press("ArrowDown")  # arrows move inside the radio group (no-op with one option)
    expect(page.get_by_role("radio", checked=True)).to_have_count(1)


@pytest.mark.parametrize("width", [375, 1280])
def test_the_sign_in_is_a_centred_card_with_the_toggle_inside_the_field(
    page: Page,
    live_server: LiveServer,
    width: int,
) -> None:
    page.set_viewport_size({"width": width, "height": 900})
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    page.goto(f"{live_server.url}/account/login/")
    page.wait_for_load_state("load")
    _check_page(page)
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
    card = page.locator(".signin-card").bounding_box()
    assert card is not None
    assert abs((card["x"] + card["width"] / 2) - width / 2) < 2, "centred"
    control = page.locator(".signin-control").bounding_box()
    toggle = page.locator(".password-toggle").bounding_box()
    assert control is not None
    assert toggle is not None
    assert control["x"] <= toggle["x"]
    assert toggle["x"] + toggle["width"] <= control["x"] + control["width"] + 1
    assert control["y"] - 1 <= toggle["y"]
    assert toggle["y"] + toggle["height"] <= control["y"] + control["height"] + 1
    page.locator("#id_username").focus()
    page.keyboard.press("Tab")
    page.keyboard.press("Tab")
    expect(page.locator(".password-toggle")).to_be_focused()
    page.keyboard.press("Space")
    expect(page.locator("#id_password")).to_have_attribute("type", "text")


def _longest_show_password() -> str:
    """The longest catalog translation of the toggle's text, whatever the languages are."""
    texts = []
    for code, _name in django_settings.LANGUAGES:
        with translation.override(code):
            texts.append(str(translation.gettext("Show password")))
    return max(texts, key=len)


@pytest.mark.parametrize("width", [375, 1280])
def test_a_long_toggle_text_and_the_error_state_never_overlap_the_input(
    page: Page,
    live_server: LiveServer,
    width: int,
) -> None:
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{live_server.url}/account/login/")
    page.wait_for_load_state("load")
    page.evaluate(
        "(text) => { const b = document.querySelector('.password-toggle'); b.textContent = text;"
        " document.querySelector('#id_password').classList.add('is-invalid'); }",
        _longest_show_password(),
    )
    control = page.locator(".signin-control").bounding_box()
    field = page.locator("#id_password").bounding_box()
    toggle = page.locator(".password-toggle").bounding_box()
    assert control is not None
    assert field is not None
    assert toggle is not None
    assert field["x"] + field["width"] <= toggle["x"] + 1, "the toggle sits beside the input"
    assert toggle["x"] + toggle["width"] <= control["x"] + control["width"] + 1
    assert toggle["y"] >= control["y"] - 1
    assert toggle["y"] + toggle["height"] <= control["y"] + control["height"] + 1
    assert field["width"] >= 100, "the input keeps room to type"
