"""ORD.19 follow-up (#757): an item without a price the buyer can see cannot go in the cart.

A `buy` product always has a net price (Q-31, a database constraint), so "no price" means no
consumer price can be decided (TAX.12): the vendor has no valid tax profile, for example. Such an
item has no buy button, is refused by the add-to-cart form, and a line whose price disappears
later is flagged on the cart page and blocks the checkout, like an unavailable product.
"""

from html import unescape
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.cart.models import Cart, CartLine
from apps.catalog.models import Product
from apps.catalog.tests.factories import (
    ProductFactory,
    ProductTranslationFactory,
    VariantFactory,
)
from apps.orders import checkout
from apps.orders import engine as orders_engine
from apps.orders.tests.integration.test_checkout_views import (  # noqa: F401 (autouse fixture)
    DETAILS,
    D,
    add,
    tax_setup,
    vendor,
    with_cart,
)
from apps.tax.models import VendorTaxProfile
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

HX = {"HX-Request": "true"}
NO_PRICE = "This item has no price yet, so you can't buy it online."


def unpriced_vendor() -> Vendor:
    """An active, online vendor without a tax profile: no consumer price can be decided."""
    return VendorFactory.create(status=Vendor.Status.ACTIVE, country="IT", sells_online=True)


def listed(owner: Vendor, **fields: Any) -> Product:
    product = ProductFactory.create(
        vendor=owner, status=Product.Status.PUBLISHED, price_net=D("10.00"), stock_qty=10,
        **fields,
    )  # fmt: skip
    ProductTranslationFactory.create(
        product=product, title="Oak sconce", slug="oak-sconce", status="published"
    )
    return product


def post_add(client: Client, product: Product, **extra: Any) -> Any:
    data = {"product": str(product.public_id), "quantity": "1", **extra}
    return client.post(reverse("orders:cart_add"), data, headers=HX)


# --- Adding


def test_an_unpriced_product_is_refused_by_the_form_and_no_line_is_made(client: Client) -> None:
    response = post_add(client, listed(unpriced_vendor()))
    html = unescape(response.content.decode())
    assert NO_PRICE in html
    assert "Added to your cart." not in html
    assert not CartLine.objects.exists()


def test_an_unpriced_variant_is_refused_without_javascript_too(client: Client) -> None:
    product = listed(unpriced_vendor(), kind=Product.Kind.VARIABLE)
    variant = VariantFactory.create(product=product, stock_qty=5)
    data = {"variant": variant.sku, "next": "/product/oak-sconce/"}
    response = client.post(
        reverse("orders:cart_add"), {"product": str(product.public_id), "quantity": "1", **data}
    )
    assert response["Location"] == "/product/oak-sconce/"
    assert not CartLine.objects.exists()


def test_a_priced_product_is_still_added(client: Client) -> None:
    post_add(client, listed(vendor()))
    assert Cart.objects.get().lines.count() == 1


# --- A price that disappears after the add


def drop_tax_profile(owner: Vendor) -> None:
    VendorTaxProfile.objects.filter(vendor=owner).delete()


def test_a_line_that_lost_its_price_is_flagged_in_the_cart(client: Client) -> None:
    cart = with_cart(client)
    owner = vendor()
    add(cart, owner, "20.00", "Oak sconce")
    drop_tax_profile(owner)
    html = client.get(reverse("orders:cart")).content.decode()
    assert "This item has no price yet." in html
    assert "Continue to checkout" not in html


def test_a_line_that_lost_its_price_blocks_the_checkout(client: Client) -> None:
    cart = with_cart(client)
    owner = vendor()
    add(cart, owner, "20.00", "Oak sconce")
    drop_tax_profile(owner)
    before = checkout.build_checkout(cart, {})
    assert orders_engine.NO_PRICE in {block.code for block in before.blocks}
    after = checkout.build_checkout(cart, DETAILS)
    assert not after.placeable


# --- The product page


def test_an_unpriced_product_has_no_buy_button(client: Client) -> None:
    listed(unpriced_vendor())
    html = unescape(client.get("/product/oak-sconce/").content.decode())
    assert reverse("orders:cart_add") not in html
    assert "Add to cart" not in html
    assert NO_PRICE in html


def test_an_unpriced_variant_product_has_no_buy_button(client: Client) -> None:
    product = listed(unpriced_vendor(), kind=Product.Kind.VARIABLE)
    VariantFactory.create(product=product, stock_qty=5)
    html = unescape(client.get("/product/oak-sconce/").content.decode())
    assert reverse("orders:cart_add") not in html
    assert "Add to cart" not in html


def test_a_priced_product_keeps_its_buy_button(client: Client) -> None:
    listed(vendor())
    html = unescape(client.get("/product/oak-sconce/").content.decode())
    assert reverse("orders:cart_add") in html
    assert NO_PRICE not in html
