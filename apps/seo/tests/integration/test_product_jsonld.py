"""SEO.1: the JSON-LD block, canonical and robots of the product page, rendered end to end."""

import json
import re
from decimal import Decimal
from typing import Any

import pytest
from django.apps import apps
from django.test import Client
from wagtail.models import Site

from apps.catalog.models import Product
from apps.catalog.tests.factories import (
    ProductFactory,
    ProductTranslationFactory,
    VariantFactory,
    VariantTranslationFactory,
)
from apps.core.i18n import configured_languages
from apps.core.models import TranslationStatus
from apps.seo import schema_check
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

SCRIPT = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)


@pytest.fixture(autouse=True)
def _site_allows_indexing() -> None:
    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )


@pytest.fixture(params=["base", "ride"])
def theme(request: pytest.FixtureRequest, settings: Any) -> str:
    settings.MPMV_THEME_DEFAULT = request.param
    return str(request.param)


def live(title: str = "Mug", **kwargs: Any) -> Product:
    kwargs.setdefault(
        "vendor", VendorFactory.create(status=Vendor.Status.ACTIVE, sells_online=True)
    )
    kwargs.setdefault("status", Product.Status.PUBLISHED)
    product = ProductFactory.create(**kwargs)
    for language in configured_languages():
        ProductTranslationFactory.create(
            product=product,
            language=language,
            title=title,
            slug=f"mug-{language}",
            description="<p>Hand made</p>",
            status=TranslationStatus.PUBLISHED,
        )
    return product


def graph_of(client: Client, path: str = "/product/mug-en/") -> tuple[str, dict[str, Any]]:
    html = client.get(path).content.decode()
    blocks = SCRIPT.findall(html)
    assert len(blocks) == 1, "a product page has exactly one JSON-LD script"
    return html, json.loads(blocks[0])


def node(document: dict[str, Any], kind: str) -> dict[str, Any]:
    found: dict[str, Any]
    [found] = [n for n in document["@graph"] if n["@type"] == kind]
    return found


def test_a_sold_product_page_has_the_google_product_properties(client: Client, theme: str) -> None:
    product = live(price_net=Decimal("12.50"), gtin="4006381333931", mpn="MUG-77")
    _html, document = graph_of(client)
    item = node(document, "Product")
    assert item["name"] == "Mug"
    assert item["sku"] == str(product.sku)
    assert item["gtin13"] == "4006381333931"
    assert item["mpn"] == "MUG-77"
    assert item["offers"]["price"] == "12.50"
    assert item["offers"]["priceCurrency"]
    assert item["offers"]["availability"] == "https://schema.org/InStock"
    assert item["offers"]["seller"]["@id"] == node(document, "Organization")["@id"]
    assert schema_check.dangling_references(document) == []
    assert schema_check.rich_result_errors(document) == []


def test_an_on_request_product_has_no_price(client: Client, theme: str) -> None:
    live(price_net=Decimal("99.00"), sales_mode=Product.SalesMode.ON_REQUEST)
    html, document = graph_of(client)
    assert "offers" not in node(document, "Product")
    assert "99.00" not in SCRIPT.findall(html)[0]
    assert "price" not in json.dumps(document).lower()


def test_a_catalog_only_product_has_no_price(client: Client, theme: str) -> None:
    live(price_net=Decimal("99.00"), sales_mode=Product.SalesMode.CATALOG_ONLY)
    _html, document = graph_of(client)
    assert "price" not in json.dumps(document).lower()


def test_a_product_with_variants_is_a_group(client: Client, theme: str) -> None:
    product = live(kind=Product.Kind.VARIABLE, price_net=Decimal("10.00"))
    variant = VariantFactory.create(product=product, price_net_override=Decimal("11.00"))
    for language in configured_languages():
        VariantTranslationFactory.create(variant=variant, language=language, label="Blue")
    _html, document = graph_of(client)
    group = node(document, "ProductGroup")
    assert [v["sku"] for v in group["hasVariant"]] == [str(variant.sku)]
    assert group["hasVariant"][0]["offers"]["price"] == "11.00"


def test_the_breadcrumb_ends_at_the_product(client: Client, theme: str) -> None:
    live()
    _html, document = graph_of(client)
    items = node(document, "BreadcrumbList")["itemListElement"]
    assert items[-1]["name"] == "Mug"
    assert [i["position"] for i in items] == list(range(1, len(items) + 1))


def test_a_hostile_name_is_inert(client: Client, theme: str) -> None:
    live(title="</script><img src=x onerror=alert(1)>")
    html = client.get("/product/mug-en/").content.decode()
    assert "<img src=x" not in html
    assert len(SCRIPT.findall(html)) == 1
    document = json.loads(SCRIPT.findall(html)[0])
    assert node(document, "Product")["name"] == "</script><img src=x onerror=alert(1)>"


def test_the_canonical_and_robots_are_one_each(client: Client, theme: str) -> None:
    live()
    html = client.get("/product/mug-en/").content.decode()
    assert html.count('rel="canonical"') == 1
    assert html.count('name="robots"') <= 1
    assert 'href="http://testserver/product/mug-en/"' in html


def test_the_product_json_ld_stays_under_the_budget(client: Client, theme: str) -> None:
    live(price_net=Decimal("12.50"), gtin="4006381333931", mpn="MUG-77")
    html = client.get("/product/mug-en/").content.decode()
    assert len(SCRIPT.findall(html)[0].encode()) < 30 * 1024


def test_shipping_and_returns_follow_the_vendor_zones_and_the_product_weight(
    client: Client, theme: str
) -> None:
    from apps.shipping.tests.factories import (
        ShippingMethodFactory,
        ShippingZoneFactory,
        WeightBandFactory,
    )

    product = live(price_net=Decimal("12.50"), weight_kg=Decimal("0.500"))
    zone = ShippingZoneFactory.create(countries=["IT", "DE"])
    method = ShippingMethodFactory.create(vendor=product.vendor, zone=zone)
    WeightBandFactory.create(method=method, cost_net=Decimal("6.90"))
    _html, document = graph_of(client)
    offer = node(document, "Product")["offers"]
    [details] = [n for n in document["@graph"] if n["@type"] == "OfferShippingDetails"]
    assert offer["shippingDetails"] == [{"@id": details["@id"]}]
    assert details["shippingRate"]["value"] == "6.90"
    assert sorted(details["shippingDestination"]["addressCountry"]) == ["DE", "IT"]
    policy = node(document, "MerchantReturnPolicy")
    assert offer["hasMerchantReturnPolicy"] == {"@id": policy["@id"]}
    assert sorted(policy["applicableCountry"]) == ["DE", "IT"]
    assert schema_check.dangling_references(document) == []
