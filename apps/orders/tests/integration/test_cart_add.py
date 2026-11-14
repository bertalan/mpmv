"""ORD.19: add to cart from the product page, in the real request cycle.

Done-when: the product page has a POST form (variants, quantity) with CSRF; the view calls
`add_to_cart`; htmx gets a fragment with the message and the count, a plain POST a redirect;
errors are shown; the cart budget applies; the button shows only for a product that sells online.
"""

from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse

from apps.cart.models import Cart
from apps.catalog.models import Product
from apps.catalog.tests.factories import (
    ProductFactory,
    ProductTranslationFactory,
    VariantFactory,
)
from apps.orders.ui import views
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

HX = {"HX-Request": "true"}


def make_product(*, online: bool = True, **fields: object) -> Product:
    vendor = VendorFactory.create(status="active", sells_online=online)
    product = ProductFactory.create(
        vendor=vendor, status=Product.Status.PUBLISHED, price_net=Decimal("10.00"),
        stock_qty=10, **fields,
    )  # fmt: skip
    ProductTranslationFactory.create(
        product=product, title="Blue vase", slug="blue-vase", status="published"
    )
    return product


def page(client: Client) -> str:
    return client.get("/product/blue-vase/").content.decode()


def test_the_product_page_has_a_post_form_when_the_vendor_sells_online(client: Client) -> None:
    make_product()
    html = page(client)
    assert 'action="' + reverse("orders:cart_add") + '"' in html
    assert "csrfmiddlewaretoken" in html
    assert 'type="submit"' in html
    assert 'name="quantity"' in html


def test_an_on_request_product_has_no_button(client: Client) -> None:
    make_product(online=False)
    html = page(client)
    assert reverse("orders:cart_add") not in html
    assert "Add to cart" not in html


def test_a_plain_post_adds_and_redirects_to_the_cart(client: Client) -> None:
    product = make_product()
    response = client.post(
        reverse("orders:cart_add"), {"product": str(product.public_id), "quantity": "2"}
    )
    assert response.status_code == 302
    assert response["Location"] == reverse("orders:cart")
    assert Cart.objects.get().lines.get().quantity == 2


def test_htmx_gets_the_message_and_the_count(client: Client) -> None:
    product = make_product()
    response = client.post(
        reverse("orders:cart_add"),
        {"product": str(product.public_id), "quantity": "3"},
        headers=HX,
    )
    html = response.content.decode()
    assert response.status_code == 200
    assert "Added to your cart." in html
    assert "3 items" in html
    assert 'id="live-region"' in html


def test_a_variant_is_chosen_by_sku(client: Client) -> None:
    product = make_product(kind=Product.Kind.VARIABLE)
    variant = VariantFactory.create(product=product, stock_qty=5)
    client.post(
        reverse("orders:cart_add"),
        {"product": str(product.public_id), "variant": variant.sku, "quantity": "1"},
    )
    assert Cart.objects.get().lines.get().variant == variant


def test_errors_are_shown_and_nothing_is_added(client: Client) -> None:
    product = make_product()
    response = client.post(
        reverse("orders:cart_add"),
        {"product": str(product.public_id), "quantity": "999"},
        headers=HX,
    )
    assert "alert--danger" in response.content.decode()
    assert not Cart.objects.filter(lines__isnull=False).exists()
    bad = client.post(
        reverse("orders:cart_add"),
        {"product": str(product.public_id), "quantity": "x"},
        headers=HX,
    )
    assert "whole number" in bad.content.decode()


def test_an_on_request_product_is_refused_on_the_server(client: Client) -> None:
    product = make_product(online=False)
    response = client.post(
        reverse("orders:cart_add"),
        {"product": str(product.public_id), "quantity": "1"},
        headers=HX,
    )
    assert "alert--danger" in response.content.decode()
    assert not Cart.objects.filter(lines__isnull=False).exists()


def test_a_plain_error_goes_back_to_the_page_and_ignores_a_foreign_next(client: Client) -> None:
    product = make_product(online=False)
    data = {"product": str(product.public_id), "quantity": "1", "next": "/product/blue-vase/"}
    assert client.post(reverse("orders:cart_add"), data)["Location"] == "/product/blue-vase/"
    data["next"] = "https://evil.example/x"
    assert client.post(reverse("orders:cart_add"), data)["Location"] == reverse("orders:cart")


def test_csrf_is_enforced() -> None:
    strict = Client(enforce_csrf_checks=True)
    assert strict.post(reverse("orders:cart_add"), {"product": "x"}).status_code == 403


def test_the_cart_budget_applies(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(views, "CART_LIMIT", 1)
    product = make_product()
    data = {"product": str(product.public_id), "quantity": "1"}
    assert client.post(reverse("orders:cart_add"), data).status_code == 302
    assert client.post(reverse("orders:cart_add"), data).status_code == 429
