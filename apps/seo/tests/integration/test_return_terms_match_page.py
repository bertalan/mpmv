"""SEO.14: the visible return terms of the product page equal the ones in its JSON-LD."""

import json
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
from apps.shipping.tests.factories import ShippingMethodFactory, ShippingZoneFactory
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import ReturnPolicyVersionFactory, VendorFactory
from tests.i18n import product_url


def _t(message: str) -> str:
    """The page is served in the primary language, whatever MPMV_LANGUAGES is."""
    with translation.override(primary_language()):
        return gettext(message)


pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("consumer_prices")]

MUG = product_url(primary_language(), f"mug-{primary_language()}")


@pytest.fixture(params=["base", "ride"])
def theme(request: pytest.FixtureRequest, settings: Any) -> str:
    settings.MPMV_THEME_DEFAULT = request.param
    return str(request.param)


def _product() -> Product:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE, sells_online=True, country="DE")
    product = ProductFactory.create(
        vendor=vendor, status=Product.Status.PUBLISHED, price_net=Decimal("12.50")
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


@pytest.mark.parametrize(
    ("payer", "fees", "sentence"),
    [
        ("vendor", "FreeReturn", "We pay the return shipping"),
        ("customer", "ReturnShippingFees", "You pay the return shipping"),
    ],
)
def test_the_page_and_the_json_ld_state_the_same_days_and_payer(
    client: Client, theme: str, payer: str, fees: str, sentence: str
) -> None:
    product = _product()
    ShippingMethodFactory.create(
        vendor=product.vendor, zone=ShippingZoneFactory.create(countries=["IT"])
    )
    ReturnPolicyVersionFactory.create(
        vendor=product.vendor,
        status="approved",
        return_window_days=45,
        return_shipping_paid_by=payer,
    )
    html = client.get(MUG).content.decode()
    ld = json.loads(
        re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)[0]
    )
    policy = next(n for n in ld["@graph"] if n["@type"] == "MerchantReturnPolicy")
    block = re.search(r'<section class="product-returns[ "].*?</section>', html, re.S)
    assert block is not None
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", block.group(0)))
    assert policy["merchantReturnDays"] == 45
    assert "45" in text
    assert policy["returnFees"].endswith(fees)
    assert _t(sentence) in text
