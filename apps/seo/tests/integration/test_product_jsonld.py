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
from apps.tax import prices
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("consumer_prices")]

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
    # TAX.12: the consumer price with VAT (an English visitor gets the operator's IT 22 %).
    assert item["offers"]["price"] == "15.25"
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
    assert group["hasVariant"][0]["offers"]["price"] == "13.42"


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
    assert details["shippingRate"]["value"] == "8.42"  # 6.90 net + 22 % VAT (TAX.12)
    assert sorted(details["shippingDestination"]["addressCountry"]) == ["DE", "IT"]
    policy = node(document, "MerchantReturnPolicy")
    assert offer["hasMerchantReturnPolicy"] == {"@id": policy["@id"]}
    assert sorted(policy["applicableCountry"]) == ["DE", "IT"]
    assert schema_check.dangling_references(document) == []


def test_a_page_with_a_brand_and_variants_has_a_whole_graph(client: Client, theme: str) -> None:
    from apps.catalog.tests.factories import (
        AttributeFactory,
        AttributeTranslationFactory,
        AttributeValueFactory,
        BrandFactory,
        BrandTranslationFactory,
        ManufacturerFactory,
        VariantOptionFactory,
    )

    maker = ManufacturerFactory.create()
    brand = BrandFactory.create(name="Acme", manufacturer=maker)
    for language in configured_languages():
        BrandTranslationFactory.create(
            brand=brand,
            language=language,
            slug=f"acme-{language}",
            status=TranslationStatus.PUBLISHED,
        )
    product = live(kind=Product.Kind.VARIABLE, price_net=Decimal("10.00"), brand=brand)
    colour = AttributeFactory.create(code="color")
    taste = AttributeFactory.create(code="taste")
    for language in configured_languages():
        AttributeTranslationFactory.create(
            attribute=taste, language=language, name="Taste", status=TranslationStatus.PUBLISHED
        )
    for sku_label in ("Red", "Blue"):
        variant = VariantFactory.create(product=product)
        for language in configured_languages():
            VariantTranslationFactory.create(variant=variant, language=language, label=sku_label)
        VariantOptionFactory.create(
            variant=variant, value=AttributeValueFactory.create(attribute=colour)
        )
        VariantOptionFactory.create(
            variant=variant, value=AttributeValueFactory.create(attribute=taste)
        )
    _html, document = graph_of(client)
    group = node(document, "ProductGroup")
    brand_node = node(document, "Brand")
    assert group["brand"] == {"@id": brand_node["@id"]}
    assert brand_node["name"] == "Acme"
    assert sorted(group["variesBy"]) == ["Taste", "https://schema.org/color"]
    ids = [variant["@id"] for variant in group["hasVariant"]]
    assert len(set(ids)) == 2
    assert all("url" not in variant for variant in group["hasVariant"])
    assert schema_check.dangling_references(document) == []
    assert schema_check.rich_result_errors(document) == []


def test_a_product_without_weight_still_has_the_return_policy(client: Client, theme: str) -> None:
    from apps.shipping.tests.factories import ShippingMethodFactory, ShippingZoneFactory

    product = live(price_net=Decimal("12.50"), weight_kg=None)
    ShippingMethodFactory.create(
        vendor=product.vendor, zone=ShippingZoneFactory.create(countries=["IT"])
    )
    _html, document = graph_of(client)
    policy = node(document, "MerchantReturnPolicy")
    assert policy["applicableCountry"] == ["IT"]
    assert policy["returnFees"] == "https://schema.org/ReturnShippingFees"
    assert policy["merchantReturnDays"] == 14
    assert not [n for n in document["@graph"] if n["@type"] == "OfferShippingDetails"]
    assert schema_check.dangling_references(document) == []


def test_an_image_carries_the_licence_fields_of_the_real_model() -> None:
    from apps.catalog.product_pages import GalleryImage
    from apps.core.models import ImageLicence
    from apps.core.tests.factories import MediaImageFactory
    from apps.seo.product_jsonld import _image

    licence = ImageLicence.objects.get(code="CC-BY-4.0")
    image = MediaImageFactory.create(
        licence=licence, author="Ada Lovelace", source_url="https://source.example/a"
    )
    data = _image(GalleryImage(image=image, alt="A mug"), "https://shop.example")
    assert data.license == licence.url
    assert data.creator == "Ada Lovelace"
    assert data.credit_text == "Ada Lovelace"
    assert data.copyright_notice == "\u00a9 Ada Lovelace"
    assert data.acquire_license_page == "https://source.example/a"
    assert data.url.startswith("https://shop.example")
    own = MediaImageFactory.create(author="")
    assert _image(GalleryImage(image=own, alt=""), "https://shop.example").copyright_notice == ""


def test_the_offer_price_is_the_visible_consumer_price_with_vat(client: Client, theme: str) -> None:
    live(price_net=Decimal("12.50"))
    html, document = graph_of(client)
    offer = node(document, "Product")["offers"]
    shown = re.search(r'class="product-price"><data value="([0-9.]+)"', html)
    assert shown is not None
    assert offer["price"] == shown.group(1) == "15.25"
    assert offer["priceSpecification"]["valueAddedTaxIncluded"] is True


def test_the_offer_follows_the_remembered_delivery_country(client: Client, theme: str) -> None:
    live(price_net=Decimal("10.00"))
    session = client.session
    session[prices.DELIVERY_COUNTRY_KEY] = "DE"  # what checkout remembers (TAX.12)
    session[prices.DELIVERY_POSTCODE_KEY] = "10115"
    session.save()
    _html, document = graph_of(client)
    assert node(document, "Product")["offers"]["price"] == "11.90"


def test_a_vendor_without_a_tax_profile_gets_no_offer_price(client: Client, theme: str) -> None:
    product = live(price_net=Decimal("12.50"))
    apps.get_model("tax", "VendorTaxProfile").objects.filter(vendor=product.vendor).delete()
    html, document = graph_of(client)
    assert "offers" not in node(document, "Product")
    assert "12.50" not in SCRIPT.findall(html)[0]
    # No seller, return policy or shipping node survives without an offer to reference them.
    assert [n["@type"] for n in document["@graph"]] == ["Product", "BreadcrumbList"]
    assert schema_check.dangling_references(document) == []


def test_a_shipping_zone_the_visitor_is_not_in_has_no_shipping_node(
    client: Client, theme: str
) -> None:
    from apps.shipping.tests.factories import (
        ShippingMethodFactory,
        ShippingZoneFactory,
        WeightBandFactory,
    )

    product = live(price_net=Decimal("12.50"), weight_kg=Decimal("0.500"))
    zone = ShippingZoneFactory.create(countries=["FR"])
    method = ShippingMethodFactory.create(vendor=product.vendor, zone=zone)
    WeightBandFactory.create(method=method, cost_net=Decimal("6.90"))
    _html, document = graph_of(client)
    assert [n for n in document["@graph"] if n["@type"] == "OfferShippingDetails"] == []
    assert "shippingDetails" not in node(document, "Product")["offers"]
    assert "6.90" not in json.dumps(document)
    assert schema_check.dangling_references(document) == []
    assert schema_check.rich_result_errors(document) == []
