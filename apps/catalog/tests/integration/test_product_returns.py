"""SEO.14: the product page states the same return terms as its JSON-LD (days, payer, methods)."""

import re
from decimal import Decimal
from typing import Any

import pytest
from django.test import Client
from django.utils import translation
from django.utils.translation import gettext

from apps.catalog.models import Product
from apps.catalog.tests.factories import ProductFactory, ProductTranslationFactory
from apps.core.i18n import configured_languages, primary_language
from apps.core.models import TranslationStatus
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import ReturnPolicyVersionFactory, VendorFactory
from tests.i18n import product_url


def _t(message: str) -> str:
    """The page is served in the primary language, whatever MPMV_LANGUAGES is."""
    with translation.override(primary_language()):
        return gettext(message)


pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("consumer_prices")]

MUG = product_url(primary_language(), f"mug-{primary_language()}")
BLOCK = re.compile(r'<section class="product-returns[ "].*?</section>', re.S)


@pytest.fixture(params=["base", "ride"])
def theme(request: pytest.FixtureRequest, settings: Any) -> str:
    settings.MPMV_THEME_DEFAULT = request.param
    return str(request.param)


def _product(**kwargs: Any) -> Product:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE, sells_online=True, country="DE")
    product = ProductFactory.create(
        vendor=vendor, status=Product.Status.PUBLISHED, **{"price_net": Decimal("12.50"), **kwargs}
    )
    for language in configured_languages():
        ProductTranslationFactory.create(
            product=product,
            language=language,
            title="Mug",
            slug=f"mug-{language}",
            status=TranslationStatus.PUBLISHED,
        )
    return product


def _returns(client: Client) -> str:
    html = client.get(MUG).content.decode()
    found = BLOCK.findall(html)
    assert len(found) == 1, "one returns block on the page"
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", found[0]))


def test_a_vendor_without_a_policy_shows_the_floor_and_the_shop_paying(
    client: Client, theme: str
) -> None:
    _product()
    text = _returns(client)
    assert "14" in text
    assert _t("We pay the return shipping") in text


def test_an_approved_policy_is_shown_with_window_payer_methods_and_link(
    client: Client, theme: str
) -> None:
    product = _product()
    ReturnPolicyVersionFactory.create(
        vendor=product.vendor,
        status="approved",
        return_window_days=30,
        return_shipping_paid_by="customer",
        return_methods=["post", "in_store"],
        returns_page_url="https://shop.example/returns",
    )
    html = client.get(MUG).content.decode()
    text = _returns(client)
    assert "30" in text
    assert _t("You pay the return shipping") in text
    assert _t("By post") in text
    assert _t("In store") in text
    assert 'href="https://shop.example/returns"' in html


def test_a_pending_policy_is_not_shown(client: Client, theme: str) -> None:
    product = _product()
    ReturnPolicyVersionFactory.create(
        vendor=product.vendor, status="pending", return_window_days=60
    )
    text = _returns(client)
    assert "60" not in text
    assert "14" in text


def test_a_product_on_request_has_no_returns_block(client: Client, theme: str) -> None:
    _product(sales_mode=Product.SalesMode.ON_REQUEST)
    assert "product-returns" not in client.get(MUG).content.decode()


def test_the_return_terms_cost_one_query(django_assert_num_queries: Any) -> None:
    from apps.catalog import product_pages

    vendor = _product().vendor
    with django_assert_num_queries(1):
        product_pages.return_terms(vendor)
