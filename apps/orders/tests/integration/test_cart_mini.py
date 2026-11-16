"""THM.15 follow-up: `orders:cart_mini`, a small cart fragment for the ride mini-cart.

It shows the item count and the lines only (no shipping or tax quote), never shows or consumes
the flash messages, and never creates a cart or a session.
"""

from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse

from apps.cart.models import Cart
from apps.catalog.models import Product
from apps.catalog.tests.factories import ProductFactory, ProductTranslationFactory
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

HX = {"HX-Request": "true"}


def make_product() -> Product:
    vendor = VendorFactory.create(status="active", sells_online=True)
    product = ProductFactory.create(
        vendor=vendor, status=Product.Status.PUBLISHED, price_net=Decimal("10.00"), stock_qty=10
    )
    ProductTranslationFactory.create(
        product=product, title="Blue vase", slug="blue-vase", status="published"
    )
    return product


def add(client: Client, quantity: int = 2) -> None:
    product = make_product()
    client.post(
        reverse("orders:cart_add"),
        {"product": str(product.public_id), "quantity": str(quantity)},
        headers=HX,
    )


@pytest.mark.usefixtures("consumer_prices")
def test_the_fragment_lists_the_lines_and_the_count(client: Client) -> None:
    add(client)
    response = client.get(reverse("orders:cart_mini"))
    html = response.content.decode()
    assert response.status_code == 200
    assert 'data-cart-count="2"' in html
    assert "Blue vase" in html
    assert "24.40" in html  # 2 x 10.00 + 22 % (TAX.12)
    assert reverse("orders:cart") in html
    assert "<html" not in html
    assert "Shipping" not in html
    assert "VAT" not in html


def test_an_empty_cart_shows_the_empty_state(client: Client) -> None:
    response = client.get(reverse("orders:cart_mini"))
    html = response.content.decode()
    assert response.status_code == 200
    assert 'data-cart-count="0"' in html
    assert "Your cart is empty" in html


def test_an_anonymous_visitor_gets_no_cart_and_no_session(client: Client) -> None:
    response = client.get(reverse("orders:cart_mini"))
    assert response.status_code == 200
    assert Cart.objects.count() == 0
    assert "sessionid" not in response.cookies
    assert not client.session.keys()


def test_a_queued_message_is_neither_shown_nor_consumed(client: Client) -> None:
    add(client, 1)
    line = Cart.objects.get().lines.get()
    client.post(reverse("orders:cart"), {f"qty_{line.public_id}": "abc"})  # queues a warning
    fragment = client.get(reverse("orders:cart_mini")).content.decode()
    assert "whole number" not in fragment
    page = client.get("/").content.decode()
    assert "Enter a whole number as the quantity." in page


def test_the_answer_is_never_cached(client: Client) -> None:
    response = client.get(reverse("orders:cart_mini"))
    assert "no-store" in response["Cache-Control"]


def test_a_post_is_refused(client: Client) -> None:
    assert client.post(reverse("orders:cart_mini")).status_code == 405
