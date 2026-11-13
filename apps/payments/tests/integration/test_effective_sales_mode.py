"""VEN.6 / D-19, derived at read time: a vendor without a payout account ready for payouts is
on request everywhere buyers meet its products, and the vendor's own on-request mode overrides
a product set to `buy`. No backfill: the answer comes from the account row (or its absence).

Lives in payments because only payments may know both the account row and the storefront.
"""

from decimal import Decimal
from typing import Any

import pytest
from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import Client
from wagtail.models import Site

from apps.cart import services as cart
from apps.cart.tests.factories import CartFactory
from apps.catalog import product_pages
from apps.catalog import services as catalog
from apps.catalog.models import Product
from apps.catalog.services import ListingFilter
from apps.catalog.tests.factories import ProductFactory, ProductTranslationFactory
from apps.core.i18n import configured_languages
from apps.core.models import TranslationStatus
from apps.payments import stripe_engine
from apps.payments.tests.factories import ConnectedAccountFactory
from apps.vendors import services as vendors
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

PRICE = Decimal("12.34")


@pytest.fixture(autouse=True)
def _site_allows_indexing() -> None:
    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )


def ready(vendor: Vendor) -> None:
    ConnectedAccountFactory.create(vendor=vendor, status=stripe_engine.ACTIVE)


def live(vendor: Vendor, **kwargs: Any) -> Product:
    kwargs.setdefault("status", Product.Status.PUBLISHED)
    kwargs.setdefault("stock_qty", 10)
    product = ProductFactory.create(vendor=vendor, price_net=PRICE, **kwargs)
    for language in configured_languages():
        ProductTranslationFactory.create(
            product=product,
            language=language,
            title=f"Mug {language}",
            slug=f"mug-{language}",
            status=TranslationStatus.PUBLISHED,
        )
    return product


def active_vendor(**fields: Any) -> Vendor:
    fields.setdefault("sales_mode_default", Vendor.SalesMode.BUY)
    return VendorFactory.create(status=Vendor.Status.ACTIVE, **fields)


def page(product: Product) -> product_pages.ProductPage:
    found = product_pages.find("mug-en", "en")
    assert found is not None
    assert found.product.pk == product.pk
    return found


def html(client: Client) -> str:
    prefix = "" if settings.LANGUAGE_CODE == "en" else "/en"
    response = client.get(f"{prefix}/product/mug-en/")
    assert response.status_code == 200
    return response.content.decode()


def priced() -> set[int]:
    found = catalog.listing_products(filters=ListingFilter(price_min=Decimal("0.00")))
    return set(found.values_list("pk", flat=True))


# --- No payout account row at all: on request, consistently


def test_a_vendor_without_an_account_row_is_on_request_everywhere(client: Client) -> None:
    vendor = active_vendor()
    product = live(vendor)
    assert vendors.effective_sales_mode(product) == "on_request"
    shown = page(product)
    assert (shown.on_request, shown.purchasable, shown.price) == (True, False, None)
    text = html(client)
    assert "Price on request" in text
    assert "12.34" not in text
    assert "Add to cart" not in text
    assert '"offers"' not in product_pages.structured_data(shown, site="https://shop.example.test")
    assert product.pk not in priced()
    with pytest.raises(ValidationError, match="on request"):
        cart.add_to_cart(CartFactory.create(), product)
    card = catalog.product_cards([product], "en")[0]
    assert card.price is None


def test_the_same_vendor_with_a_ready_account_sells_online(client: Client) -> None:
    vendor = active_vendor()
    ready(vendor)
    product = live(vendor)
    assert vendors.effective_sales_mode(product) == "buy"
    shown = page(product)
    assert (shown.on_request, shown.purchasable, shown.price) == (False, True, PRICE)
    text = html(client)
    assert "12.34" in text
    assert "Price on request" not in text
    assert product.pk in priced()
    cart.add_to_cart(CartFactory.create(), product)
    assert catalog.product_cards([product], "en")[0].price == PRICE


@pytest.mark.parametrize("status", [stripe_engine.PENDING, stripe_engine.RESTRICTED])
def test_an_account_not_ready_for_payouts_keeps_the_vendor_on_request(status: str) -> None:
    vendor = active_vendor()
    ConnectedAccountFactory.create(vendor=vendor, status=status)
    product = live(vendor)
    assert page(product).on_request
    assert product.pk not in priced()
    with pytest.raises(ValidationError):
        cart.add_to_cart(CartFactory.create(), product)


# --- The vendor's own on-request mode overrides a product set to buy


def test_the_vendors_on_request_mode_overrides_a_buy_product(client: Client) -> None:
    vendor = active_vendor(sales_mode_default=Vendor.SalesMode.ON_REQUEST)
    ready(vendor)
    product = live(vendor, sales_mode=Product.SalesMode.BUY)
    shown = page(product)
    assert (shown.on_request, shown.purchasable) == (True, False)
    assert "Price on request" in html(client)
    assert product.pk not in priced()
    with pytest.raises(ValidationError, match="on request"):
        cart.add_to_cart(CartFactory.create(), product)


def test_the_buy_filter_is_one_query_for_many_vendors(
    django_assert_num_queries: Any,
) -> None:
    for _ in range(3):
        vendor = active_vendor()
        ready(vendor)
        live_without_text = ProductFactory.create(
            vendor=vendor, status=Product.Status.PUBLISHED, price_net=PRICE
        )
        assert live_without_text.pk
    with django_assert_num_queries(1):
        assert len(priced()) == 3
