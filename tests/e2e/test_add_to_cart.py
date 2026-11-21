"""E2E (ORD.19): from the product page to the payment page, in a real browser.

Done when: the product page's form adds to the cart without a reload (htmx message and count);
the visitor then goes through the cart and checkout to the payment page (the fake provider; no
card data, no Stripe). Only the VIES client is replaced, as in the ORD.5 e2e.
"""

from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.catalog.models import Product
from apps.catalog.tests.factories import ProductFactory, ProductTranslationFactory
from apps.core.tests.factories import PlatformOperatorFactory, utc
from apps.orders.models import Order
from apps.orders.tests.integration.test_checkout_views import vendor
from apps.tax import engine as tax
from apps.tax.tests.factories import TaxRateFactory
from apps.vendors.tests.factories import CommissionRuleFactory
from tests.e2e.csp import RECORD_CSP_VIOLATIONS
from tests.e2e.test_checkout import TERMS, cart_link_name, check_page, fill_delivery, pay_button
from tests.i18n import localized, translated

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def product(settings: Any) -> Product:
    PlatformOperatorFactory.create(open_ended=True, valid_from=utc(2026, 1, 1), country="IT")
    TaxRateFactory.create(
        country="IT", category=tax.STANDARD, rate=Decimal("22"), valid_from=utc(2026, 1, 1)
    )
    settings.VIES_CLIENT = "apps.tax.tests.fakes.ValidViesClient"
    CommissionRuleFactory.create(valid_from=utc(2026, 1, 1), valid_to=None)
    item = ProductFactory.create(
        vendor=vendor("Rossi Ceramics"), status=Product.Status.PUBLISHED,
        price_net=Decimal("100.00"), stock_qty=50, weight_kg=Decimal("0.500"),
    )  # fmt: skip
    ProductTranslationFactory.create(
        product=item, title="Blue vase", slug="blue-vase", status="published"
    )
    return item


def test_a_visitor_goes_from_the_product_page_to_the_payment_page(
    page: Page, live_server: LiveServer, product: Product, shot: Callable[[str], Path]
) -> None:
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    product_page = f"{live_server.url}{localized('/product/blue-vase/')}"
    page.goto(product_page)
    page.locator("#product-quantity").fill("2")
    shot("1-product")
    check_page(page)
    page.get_by_role("button", name=translated("Add to cart")).click()
    expect(page.locator("#add-to-cart-result")).to_contain_text(translated("Added to your cart."))
    expect(page.get_by_role("link", name=cart_link_name(2))).to_be_visible()
    expect(page).to_have_url(product_page)  # no reload, no redirect
    shot("2-added")

    page.get_by_role("link", name=cart_link_name(2)).click()
    expect(page.get_by_role("heading", level=1)).to_have_text(translated("Your cart"))
    page.get_by_role("link", name=translated("Continue to checkout")).click()
    fill_delivery(page)
    page.locator("#id_city").press("Enter")
    expect(page.get_by_role("heading", level=1)).to_have_text(translated("Review and pay"))
    page.get_by_label(translated(TERMS)).check()  # ORD.17 box 1
    pay_button(page).click()
    expect(page.get_by_role("heading", level=1)).to_have_text(translated("Order received"))
    check_page(page)
    assert Order.objects.get().grand_total == Decimal("256.08")  # 2 x 100 + 9.90, VAT 22%
