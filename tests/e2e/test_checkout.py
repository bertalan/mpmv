"""E2E (ORD.5): the cart and checkout in a real browser, B2C and B2B, with screenshots.

Done when: "Checkout keyboard-accessible". Both paths run from the cart to the confirmation with
the keyboard-reachable controls only; every page passes axe (WCAG 2.2 AA) and raises no CSP
violation. VIES is a fake `ViesClient`, the only boundary replaced: the commission rule (VEN.2)
is a real platform default row, and the pages, `build_checkout` and `place_order` are real.
"""

import re
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.cart.models import Cart
from apps.cart.session import GUEST_CART_SESSION_KEY
from apps.cart.tests.factories import CartFactory
from apps.core.tests.factories import PlatformOperatorFactory, utc
from apps.orders.models import Order
from apps.orders.tests.integration.test_checkout_views import add, vendor
from apps.tax import engine as tax
from apps.tax.tests.factories import TaxRateFactory
from apps.vendors.tests.factories import CommissionRuleFactory
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def shop(settings: Any) -> Cart:
    """A guest cart with a vendor's goods, the tax rates, a fake VIES and the default rule."""
    PlatformOperatorFactory.create(open_ended=True, valid_from=utc(2026, 1, 1), country="IT")
    for country, rate in (("IT", "22"), ("DE", "19")):
        TaxRateFactory.create(
            country=country, category=tax.STANDARD, rate=Decimal(rate), valid_from=utc(2026, 1, 1)
        )
    settings.VIES_CLIENT = "apps.tax.tests.fakes.ValidViesClient"
    CommissionRuleFactory.create(valid_from=utc(2026, 1, 1), valid_to=None)
    cart = CartFactory.create()
    add(cart, vendor("Rossi Ceramics"), "100.00", "Blue vase")
    return cart


def open_with_cart(page: Page, live_server: LiveServer, cart: Cart) -> None:
    store = SessionStore()
    store[GUEST_CART_SESSION_KEY] = cart.session_key
    store.save()
    page.context.add_cookies(
        [{"name": settings.SESSION_COOKIE_NAME, "value": store.session_key or "",
          "url": live_server.url}]
    )  # fmt: skip
    page.add_init_script(RECORD_CSP_VIOLATIONS)


def check_page(page: Page) -> None:
    violations = run_axe(page)
    assert violations == [], format_violations(violations)
    assert csp_violations(page) == [], "\n".join(csp_violations(page))


def fill_delivery(page: Page, *, country: str = "IT") -> None:
    page.locator("#id_email").fill("buyer@example.com")
    page.locator("#id_full_name").fill("Mario Rossi")
    page.locator("#id_line1").fill("1 Via Test")
    page.locator("#id_postcode").fill("00100" if country == "IT" else "10115")
    page.locator("#id_city").fill("Roma" if country == "IT" else "Berlin")
    page.locator("#id_country").select_option(country)


def test_a_private_buyer_goes_from_the_cart_to_the_confirmation(
    page: Page, live_server: LiveServer, shop: Cart, shot: Callable[[str], Path]
) -> None:
    open_with_cart(page, live_server, shop)
    page.goto(f"{live_server.url}/cart/")
    expect(page.get_by_role("heading", level=1)).to_have_text("Your cart")
    expect(page.get_by_role("heading", level=2, name="Sold by Rossi Ceramics")).to_be_visible()
    shot("1-cart")
    check_page(page)

    page.locator(".quantity input").fill("2")
    page.locator(".quantity input").dispatch_event("change")
    expect(page.locator("#live-region")).to_have_text("Cart updated.")
    expect(page.locator(".quantity input")).to_have_value("2")

    page.get_by_role("link", name="Continue to checkout").click()
    expect(page.get_by_role("heading", level=1)).to_have_text("Your details")
    expect(page.locator("#id_company")).to_be_hidden()  # a private buyer sees no business fields
    fill_delivery(page)
    shot("2-details")
    check_page(page)
    page.locator("#id_city").press("Enter")  # submit from the keyboard

    expect(page.get_by_role("heading", level=1)).to_have_text("Review and pay")
    expect(page.get_by_role("radio")).to_be_checked()
    expect(page.locator(".order-summary").last).to_contain_text("256.08")  # 2 x 100 + 9.90, VAT 22%
    shot("3-review")
    check_page(page)
    # ORD.17: box 1 is unticked and mandatory; the button names the obligation to pay.
    pay = page.get_by_role("button", name=re.compile("Confirm order and pay now"))
    expect(page.get_by_label(re.compile("I have read and accept"))).not_to_be_checked()
    expect(page.get_by_label(re.compile("specifically approve"))).to_have_count(0)  # a consumer
    expect(pay).to_contain_text("256.08")
    page.get_by_label(re.compile("I have read and accept")).check()
    pay.click()

    expect(page.get_by_role("heading", level=1)).to_have_text("Order received")
    expect(page.get_by_role("button", name="Pay now")).to_be_disabled()
    shot("4-confirmation")
    check_page(page)
    assert Order.objects.get().grand_total == Decimal("256.08")


def test_a_business_buyer_enters_company_and_vat_id_and_vies_confirms_it(
    page: Page, live_server: LiveServer, shop: Cart, shot: Callable[[str], Path]
) -> None:
    open_with_cart(page, live_server, shop)
    page.goto(f"{live_server.url}/checkout/")
    fill_delivery(page, country="DE")
    page.locator("#id_is_business").check()  # reveals the business fields
    expect(page.locator("#id_company")).to_be_visible()
    page.locator("#id_company").fill("Acme GmbH")
    page.locator("#id_vat_id").fill("DE123456789")
    shot("1-details-business")
    check_page(page)
    page.get_by_role("button", name="Continue to review").click()

    expect(page.get_by_role("heading", level=1)).to_have_text("Review and pay")
    expect(page.get_by_text("Your VAT number is valid")).to_be_visible()
    expect(page.get_by_text("Acme GmbH")).to_be_visible()
    shot("2-review-business")
    check_page(page)


def test_a_blocked_package_explains_itself_and_offers_a_quote(
    page: Page, live_server: LiveServer, shop: Cart, shot: Callable[[str], Path]
) -> None:
    add(shop, vendor("Heavy Metal Shop"), "10.00", "Anvil", weight_kg=Decimal("12.000"))
    open_with_cart(page, live_server, shop)
    page.goto(f"{live_server.url}/checkout/")
    fill_delivery(page)
    page.get_by_role("button", name="Continue to review").click()
    expect(page.get_by_text("needs a quote from the vendor")).to_be_visible()
    expect(page.get_by_role("link", name="Request a quote")).to_be_visible()
    expect(page.get_by_role("button", name=re.compile("Confirm order and pay now"))).to_have_count(
        0
    )
    shot("blocked-review")
    check_page(page)
