"""ORD.19 follow-up (#757): an item without a price the buyer can see cannot go in the cart.

A `buy` product always has a net price (Q-31, a database constraint), so "no price" means no
consumer price can be decided (TAX.12): the vendor has no valid tax profile, for example. Such an
item has no buy button, is refused by the add-to-cart form, and a line whose price disappears
later is flagged on the cart page and blocks the checkout, like an unavailable product.
"""

import secrets
from decimal import Decimal
from html import unescape
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.cart import services as cart_services
from apps.cart.models import Cart, CartLine
from apps.cart.tests.factories import CartFactory, CartLineFactory
from apps.catalog.models import Product
from apps.catalog.tests.factories import (
    ProductFactory,
    ProductTranslationFactory,
    VariantFactory,
)
from apps.core.models import AuditLog
from apps.core.tests.factories import utc
from apps.orders import checkout, reorder, staff_drafts
from apps.orders import engine as orders_engine
from apps.orders.tests.integration.test_checkout_views import (  # noqa: F401 (autouse fixture)
    DETAILS,
    D,
    add,
    tax_setup,
    vendor,
    with_cart,
)
from apps.orders.tests.integration.test_order_again import CTX, order_of, signed_in
from apps.orders.tests.integration.test_staff_drafts import draft_of, open_with, sent_token
from apps.tax import prices as tax_prices
from apps.tax.models import VendorTaxProfile
from apps.tax.tests.factories import VendorTaxProfileFactory
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

HX = {"HX-Request": "true"}
NO_PRICE = "This item has no price yet, so you can't buy it online."


def unpriced_vendor() -> Vendor:
    """An active, online vendor without a tax profile: no consumer price can be decided."""
    return VendorFactory.create(status=Vendor.Status.ACTIVE, country="IT", sells_online=True)


def listed(owner: Vendor, *, slug: str = "oak-sconce", **fields: Any) -> Product:
    product = ProductFactory.create(
        vendor=owner, status=Product.Status.PUBLISHED, price_net=D("10.00"), stock_qty=10,
        **fields,
    )  # fmt: skip
    ProductTranslationFactory.create(
        product=product, title="Oak sconce", slug=slug, status="published"
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


# --- The services and the other add paths (review of #915)


def test_the_cart_services_refuse_an_unpriced_item_with_their_code() -> None:
    product = listed(unpriced_vendor())
    cart = CartFactory.create()
    with pytest.raises(ValidationError) as single:
        cart_services.add_to_cart(cart, product)
    assert single.value.code == cart_services.NO_PRICE
    with pytest.raises(ValidationError):
        cart_services.add_many_to_cart(cart, [(product, None, 1)], ctx=CTX)
    assert not CartLine.objects.exists()


def test_a_purchase_error_wins_over_the_missing_price(client: Client) -> None:
    owner = VendorFactory.create(status=Vendor.Status.ACTIVE, country="IT", sells_online=False)
    html = unescape(post_add(client, listed(owner)).content.decode())
    assert "available on request only" in html
    assert NO_PRICE not in html


def test_order_again_skips_an_item_that_lost_its_price() -> None:
    _, user = signed_in()
    owner = vendor()
    item = listed(owner)
    order = order_of(user, [(item, 1)])
    drop_tax_profile(owner)
    result = reorder.order_again(user, order.public_id, ctx=CTX)
    assert result is not None
    assert not result.added
    assert [s.reason for s in result.skipped] == [orders_engine.REORDER_NO_PRICE]
    assert not CartLine.objects.exists()


def test_a_staff_draft_link_skips_an_item_that_lost_its_price() -> None:
    owner = vendor()
    item = listed(owner)
    draft = draft_of(order_of(None, [(item, 1)]))
    token = sent_token(draft)
    drop_tax_profile(owner)
    guest = Client()
    open_with(guest, token)
    assert not CartLine.objects.exists()
    opened = AuditLog.objects.filter(action=staff_drafts.ACTION_OPENED).get()
    after = opened.after or {}
    assert (after["added"], after["skipped"]) == (0, 1)


# --- One destination for the page and the cart, read once


def oss_vendor() -> Vendor:
    """An OSS vendor: its price carries the destination's rate (no FR rate in this setup)."""
    owner = VendorFactory.create(status=Vendor.Status.ACTIVE, country="IT", sells_online=True)
    VendorTaxProfileFactory.create(vendor=owner, oss=True, valid_from=utc(2026, 1, 1))
    return owner


@pytest.mark.parametrize(("country", "priced"), [("DE", True), ("FR", False)])
def test_the_page_and_the_cart_agree_for_a_remembered_country(
    client: Client, country: str, priced: bool
) -> None:
    product = listed(oss_vendor())
    cart = with_cart(client)
    CartLineFactory.create(cart=cart, product=product, added_price_net=D("10.00"))
    session = client.session
    session[tax_prices.DELIVERY_COUNTRY_KEY] = country
    session.save()
    page = unescape(client.get("/product/oak-sconce/").content.decode())
    assert (reverse("orders:cart_add") in page) is priced
    cart_page = client.get(reverse("orders:cart")).content.decode()
    assert ("This item has no price yet." in cart_page) is not priced


class AlwaysPriced(tax_prices.PriceCheck):
    def priced(self, net: Decimal | None, fiscal_category: str, vendor: Vendor) -> bool:
        return True


def test_the_price_check_of_the_cart_costs_the_same_for_one_line_and_five() -> None:
    """The tax tables are read once per cart, not per line: the queries the check adds to the
    preview do not grow with the lines (the preview's own per-line reads are not this PR's)."""
    owner = vendor()

    def queries(count: int, prices: tax_prices.PriceCheck | None) -> int:
        cart = CartFactory.create()
        for _ in range(count):
            item = listed(owner, slug=f"sconce-{secrets.token_hex(4)}")
            CartLineFactory.create(cart=cart, product=item, added_price_net=D("10.00"))
        with CaptureQueriesContext(connection) as ctx:
            checkout.build_checkout(cart, {}, prices=prices)
        return len(ctx)

    queries(1, None)  # warms the process caches (operator settings, flags)
    always = AlwaysPriced()

    def added(count: int) -> int:
        return queries(count, None) - queries(count, always)

    assert added(5) == added(1)
