"""TAX.12 follow-up: the checkout shows consumer prices with VAT (Directive 2011/83 art. 6(1)(e):
the total price inclusive of taxes). Shipping options and the review summary carry their VAT;
the net rows only for a business buyer who opted in to net prices. The catalog's `book_art74`
category reaches the tax engine as `books` (it raised an uncaught ValueError before).
"""

import datetime as dt
import re
from decimal import Decimal

import pytest
from bs4 import BeautifulSoup
from django.test import Client
from django.urls import reverse
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.cart.tests.factories import CartFactory
from apps.core.tests.factories import utc
from apps.orders import checkout
from apps.orders.tests.integration.test_checkout_views import (
    NBSP,
    add,
    sign_off_details,
    tax_setup,
    vendor,
    with_cart,
)
from apps.tax import engine as tax
from apps.tax import prices
from apps.tax.models import VendorTaxProfile
from apps.tax.tests.factories import TaxRateFactory, VendorTaxProfileFactory

pytestmark = pytest.mark.django_db

__all__ = ["tax_setup"]  # the autouse fixture of the ORD.5 tests: operator and rates


def text(html: str) -> str:
    return re.sub(r"[ \t\r\n]+", " ", BeautifulSoup(html, "html.parser").get_text(" ", strip=True))


@pytest.mark.parametrize("theme", ["base", "ride"])
def test_the_review_shows_shipping_and_totals_with_vat(
    client: Client, settings: SettingsWrapper, theme: str
) -> None:
    settings.MPMV_THEME_DEFAULT = theme
    cart = with_cart(client)
    add(cart, vendor("Alpha Shop"), "100.00")
    sign_off_details(client)
    shown = text(client.get(reverse("orders:review")).content.decode())
    # Shipping 9.90 follows the goods at 22 %: 12.08. Goods 100.00 + 22.00 = 122.00.
    assert f"€{NBSP}12.08" in shown
    assert f"€{NBSP}9.90" not in shown
    assert f"Goods, VAT included €{NBSP}122.00" in shown
    assert f"Shipping, VAT included €{NBSP}12.08" in shown
    assert f"Of which VAT €{NBSP}24.18" in shown
    assert f"€{NBSP}134.08" in shown
    assert "excluding VAT" not in shown


@pytest.mark.parametrize("theme", ["base", "ride"])
def test_the_cart_summary_with_an_address_is_vat_inclusive(
    client: Client, settings: SettingsWrapper, theme: str
) -> None:
    settings.MPMV_THEME_DEFAULT = theme
    cart = with_cart(client)
    add(cart, vendor(), "100.00")
    sign_off_details(client)
    shown = text(client.get(reverse("orders:cart")).content.decode())
    assert f"€{NBSP}122.00" in shown  # the line, with VAT
    assert f"Goods, VAT included €{NBSP}122.00" in shown
    assert "excluding VAT" not in shown


def test_a_business_buyer_who_opted_in_sees_the_net_rows(client: Client) -> None:
    cart = with_cart(client)
    add(cart, vendor("Alpha Shop"), "100.00")
    sign_off_details(client)
    session = client.session
    session[prices.BUSINESS_NET_VIEW_KEY] = True
    session.save()
    shown = text(client.get(reverse("orders:review")).content.decode())
    assert f"€{NBSP}9.90 excl. VAT" in shown
    assert f"Goods, excluding VAT €{NBSP}100.00" in shown
    assert f"€{NBSP}134.08" in shown  # the total still includes VAT


def test_the_checkout_address_is_remembered_for_the_shop_prices(client: Client) -> None:
    cart = with_cart(client)
    add(cart, vendor(), "100.00")
    sign_off_details(client, country="IT", postcode="23041")
    session = client.session
    assert session[prices.DELIVERY_COUNTRY_KEY] == "IT"
    assert session[prices.DELIVERY_POSTCODE_KEY] == "23041"


def test_a_book_art74_product_is_priced_at_checkout_with_the_books_rate(client: Client) -> None:
    """Regression: the catalog's `book_art74` reached `resolve_goods_tax` as is and raised an
    uncaught ValueError; it is the engine's `books` category."""
    TaxRateFactory.create(
        country="IT", category=tax.BOOKS, rate=Decimal("4"), valid_from=utc(2026, 1, 1)
    )
    cart = with_cart(client)
    add(cart, vendor(), "10.00", title="Atlas", fiscal_category="book_art74")
    sign_off_details(client)
    response = client.get(reverse("orders:review"))
    assert response.status_code == 200
    assert f"€{NBSP}10.40" in text(response.content.decode())  # 10.00 + 4 %


# --- The shown price is the checkout price (lane-b's follow-up on #565). Territories are not
# compared here: shipping to Livigno needs a quote, so the preview prices no line there.


@pytest.mark.parametrize(
    ("regime", "country", "postcode", "category", "net"),
    [
        ("domestic_only", "IT", "00100", "standard", "100.00"),
        ("domestic_only", "DE", "10115", "standard", "100.00"),  # the vendor's 22 %, not 19 %
        ("oss", "DE", "10115", "standard", "12.50"),  # 19 %, half a cent up
        ("oss", "IT", "00100", "reduced", "0.05"),
        ("exempt", "DE", "10115", "standard", "100.00"),  # forfettario: no VAT
        ("exempt", "IT", "00100", "reduced", "10.00"),
    ],
)
def test_consumer_price_equals_the_checkout_preview(
    regime: str, country: str, postcode: str, category: str, net: str
) -> None:
    owner = vendor()
    VendorTaxProfile.objects.filter(vendor=owner).delete()
    VendorTaxProfileFactory.create(
        vendor=owner,
        regime=regime,
        exempt_reason="flat_rate_forfettario" if regime == "exempt" else "",
        oss_registered_since=dt.date(2026, 1, 1) if regime == "oss" else None,
        valid_from=utc(2026, 1, 1),
    )
    cart = CartFactory.create()
    add(cart, owner, net, fiscal_category=category)
    address = {"line1": "1 Test", "city": "Test", "postcode": postcode, "country": country}
    preview = checkout.build_checkout(cart, address)
    line = preview.packages[0].lines[0]
    assert line.tax_rate is not None, preview.packages[0].blocks
    assert line.tax is not None
    shown = prices.consumer_price(
        Decimal(net), category, vendor=owner, country=country, postcode=postcode
    )
    assert shown.rate == line.tax_rate
    assert shown.gross == line.unit_price_net + line.tax  # quantity 1: unit = line
