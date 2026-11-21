"""E2E (THM.8 1/2): cart and checkout with the `ride` theme, by keyboard only.

Done when: "A keyboard-only e2e checkout completes in test mode with `ride`; the Payment Element
uses the theme's colours and fonts". Tab, Enter and Space only: no mouse click anywhere. Stripe.js
is the one boundary replaced: a fake script answers the request for js.stripe.com, mounts a card
field, records the `appearance` it was given and confirms by sending the browser to the return URL
(the server side is the repository's `FakeStripeClient`). Every page passes axe and the CSP, at
360, 768 and 1280 px.
"""

import dataclasses
import re
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Locator, Page, Route, expect
from pytest_django.live_server_helper import LiveServer

from apps.cart.models import Cart
from apps.payments.tests.fake_stripe import FakeStripeClient
from apps.payments.tests.integration.test_order_payment import stripe  # noqa: F401 (fixture)
from tests.e2e.test_checkout import check_page as _check_page
from tests.e2e.test_checkout import open_with_cart, shop  # noqa: F401 (fixture)

pytestmark = pytest.mark.django_db(transaction=True)

FAKE_STRIPE_JS = """
window.Stripe = function (key) {
  return {
    elements: function (options) {
      window.__appearance = options.appearance;
      return {
        create: function () {
          return {
            mount: function (selector) {
              var host = document.querySelector(selector);
              host.innerHTML = '<label>Card number ' +
                '<input id="fake-card" autocomplete="off"></label>';
            }
          };
        }
      };
    },
    confirmPayment: function (args) {
      return fetch('/__stripe_confirm__').then(function () {
        window.location.href = args.confirmParams.return_url;
        return {};
      });
    }
  };
};
"""

WIDTHS = (360, 768, 1280)


def check_page(page: Page) -> None:
    page.wait_for_load_state("load")  # the stylesheet is in: axe measures the styled page
    _check_page(page)


@pytest.fixture(autouse=True)
def _ride(settings: Any) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    settings.STRIPE_PUBLISHABLE_KEY = "pk_" + secrets.token_urlsafe(12)


def tab_to(page: Page, target: Locator, *, limit: int = 120) -> None:
    """Move the focus to `target` with the Tab key only."""
    for _ in range(limit):
        if target.evaluate("el => el === document.activeElement"):
            return
        page.keyboard.press("Tab")
    raise AssertionError(f"{target} was not reached with {limit} Tab presses")


def fake_stripe_routes(page: Page, stripe: FakeStripeClient) -> None:  # noqa: F811
    def js(route: Route) -> None:
        route.fulfill(status=200, content_type="text/javascript", body=FAKE_STRIPE_JS)

    def confirm(route: Route) -> None:
        for key, intent in list(stripe.intents.items()):
            stripe.intents[key] = dataclasses.replace(intent, status="processing")
        route.fulfill(status=204)

    page.route(re.compile(r"https://js\.stripe\.com/.*"), js)
    page.route(re.compile(r".*/__stripe_confirm__"), confirm)


def test_a_keyboard_only_checkout_completes_with_ride(
    page: Page,
    live_server: LiveServer,
    shop: Cart,  # noqa: F811
    stripe: FakeStripeClient,  # noqa: F811
    shot: Callable[[str], Path],
) -> None:
    fake_stripe_routes(page, stripe)
    open_with_cart(page, live_server, shop)
    page.goto(f"{live_server.url}/cart/")
    expect(page.locator("html")).to_have_attribute("data-theme", "ride")
    check_page(page)
    shot("1-cart")

    link = page.get_by_role("link", name="Continue to checkout")
    tab_to(page, link)
    page.keyboard.press("Enter")
    expect(page.get_by_role("heading", level=1)).to_have_text("Your details")

    for field, value in (
        ("#id_email", "buyer@example.com"),
        ("#id_full_name", "Mario Rossi"),
        ("#id_line1", "1 Via Test"),
        ("#id_postcode", "00100"),
        ("#id_city", "Roma"),
    ):
        tab_to(page, page.locator(field))
        page.keyboard.type(value)
    tab_to(page, page.locator("#id_country"))
    page.keyboard.type("Ital")  # a focused select picks the option by its first letters
    expect(page.locator("#id_country")).to_have_value("IT")
    check_page(page)
    shot("2-details")
    tab_to(page, page.get_by_role("button", name="Continue to review"))
    page.keyboard.press("Enter")

    expect(page.get_by_role("heading", level=1)).to_have_text("Review and pay")
    check_page(page)
    shot("3-review")
    terms = page.get_by_label(re.compile("I have read and accept"))
    tab_to(page, terms)
    page.keyboard.press("Space")
    expect(terms).to_be_checked()
    pay = page.get_by_role("button", name=re.compile("Confirm order and pay now"))
    tab_to(page, pay)
    page.keyboard.press("Enter")

    expect(page.get_by_role("heading", level=1)).to_have_text("Order received")
    card = page.locator("#fake-card")
    tab_to(page, card)
    page.keyboard.type("4242424242424242")
    appearance = page.evaluate("() => window.__appearance")
    assert appearance["variables"]["colorPrimary"] == "#C40417"
    assert "Barlow" in appearance["variables"]["fontFamily"]
    check_page(page)
    shot("4-payment")
    button = page.get_by_role("button", name="Pay now")
    tab_to(page, button)
    page.keyboard.press("Enter")

    expect(page.get_by_text("Payment received, confirming")).to_be_visible()
    check_page(page)
    shot("5-confirming")


@pytest.mark.parametrize("width", WIDTHS)
def test_the_cart_and_the_details_step_pass_the_gates_at_every_width(
    page: Page,
    live_server: LiveServer,
    shop: Cart,  # noqa: F811
    width: int,
) -> None:
    page.set_viewport_size({"width": width, "height": 900})
    open_with_cart(page, live_server, shop)
    for path in ("/cart/", "/checkout/"):
        page.goto(f"{live_server.url}{path}")
        check_page(page)
        assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")


def _gates(page: Page, width: int) -> None:
    check_page(page)
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth"), (
        f"sideways scroll at {width}px"
    )


@pytest.mark.parametrize("width", WIDTHS)
def test_review_payment_and_confirming_pass_the_gates_at_every_width(
    page: Page,
    live_server: LiveServer,
    shop: Cart,  # noqa: F811
    stripe: FakeStripeClient,  # noqa: F811
    width: int,
) -> None:
    """THM.16 follow-up: one checkout per width, each later step under the same gates."""
    fake_stripe_routes(page, stripe)
    page.set_viewport_size({"width": width, "height": 900})
    open_with_cart(page, live_server, shop)
    page.goto(f"{live_server.url}/checkout/")
    for field, value in (
        ("#id_email", "buyer@example.com"),
        ("#id_full_name", "Mario Rossi"),
        ("#id_line1", "1 Via Test"),
        ("#id_postcode", "00100"),
        ("#id_city", "Roma"),
    ):
        page.locator(field).fill(value)
    page.locator("#id_country").select_option("IT")
    page.locator("#id_city").press("Enter")

    expect(page.get_by_role("heading", level=1)).to_have_text("Review and pay")
    _gates(page, width)
    page.get_by_label(re.compile("I have read and accept")).check()
    page.get_by_role("button", name=re.compile("Confirm order and pay now")).click()

    expect(page.get_by_role("heading", level=1)).to_have_text("Order received")
    expect(page.locator("#fake-card")).to_be_visible()
    _gates(page, width)
    page.get_by_role("button", name="Pay now").click()
    expect(page.get_by_text("Payment received, confirming")).to_be_visible()
    _gates(page, width)
