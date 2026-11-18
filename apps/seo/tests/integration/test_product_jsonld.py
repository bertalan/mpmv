"""SEO.1: the JSON-LD block, canonical and robots of the product page, rendered end to end."""

import json
import re
from decimal import Decimal
from typing import Any

import pytest
from django.apps import apps
from django.conf import settings
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from wagtail.models import Site

from apps.catalog.models import Product
from apps.catalog.tests.factories import (
    ProductFactory,
    ProductTranslationFactory,
    VariantFactory,
    VariantTranslationFactory,
)
from apps.core.i18n import configured_languages, primary_language
from apps.core.models import TranslationStatus
from apps.seo import schema_check
from apps.tax import prices
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory
from tests.i18n import product_url

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


MUG = product_url(primary_language(), f"mug-{primary_language()}")


def graph_of(client: Client, path: str = "") -> tuple[str, dict[str, Any]]:
    html = client.get(path or MUG).content.decode()
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
    seller = [n for n in document["@graph"] if n["@id"] == item["offers"]["seller"]["@id"]]
    assert [n["@type"] for n in seller] == ["Organization"]
    assert seller[0]["@id"] != "http://localhost/#organization"  # the vendor, never the operator
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
    html = client.get(MUG).content.decode()
    assert "<img src=x" not in html
    assert len(SCRIPT.findall(html)) == 1
    document = json.loads(SCRIPT.findall(html)[0])
    assert node(document, "Product")["name"] == "</script><img src=x onerror=alert(1)>"


def test_the_canonical_and_robots_are_one_each(client: Client, theme: str) -> None:
    live()
    html = client.get(MUG).content.decode()
    assert html.count('rel="canonical"') == 1
    assert html.count('name="robots"') <= 1
    root = settings.WAGTAILADMIN_BASE_URL.rstrip("/")  # the base URL, not the Site row
    assert f'href="{root}{MUG}"' in html


def test_the_product_json_ld_stays_under_the_budget(client: Client, theme: str) -> None:
    live(price_net=Decimal("12.50"), gtin="4006381333931", mpn="MUG-77")
    html = client.get(MUG).content.decode()
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
    assert policy["returnFees"] == "https://schema.org/FreeReturn"  # Q-105 default
    assert "merchantReturnLink" not in policy  # the default has no returns page
    assert policy["merchantReturnDays"] == 14
    assert not [n for n in document["@graph"] if n["@type"] == "OfferShippingDetails"]
    assert schema_check.dangling_references(document) == []


def _sold_product(country: str = "DE") -> Product:
    from apps.shipping.tests.factories import ShippingMethodFactory, ShippingZoneFactory

    product = live(
        price_net=Decimal("12.50"),
        weight_kg=None,
        vendor=VendorFactory.create(
            status=Vendor.Status.ACTIVE, sells_online=True, country=country
        ),
    )
    ShippingMethodFactory.create(
        vendor=product.vendor, zone=ShippingZoneFactory.create(countries=["IT"])
    )
    return product


def _policy(document: dict[str, Any]) -> dict[str, Any]:
    assert schema_check.dangling_references(document) == []
    assert schema_check.rich_result_errors(document) == []
    return node(document, "MerchantReturnPolicy")


def test_an_approved_policy_states_its_window_link_country_methods_and_payer(
    client: Client, theme: str
) -> None:
    from apps.vendors.tests.factories import ReturnPolicyVersionFactory

    product = _sold_product()
    ReturnPolicyVersionFactory.create(
        vendor=product.vendor,
        status="approved",
        return_window_days=30,
        return_shipping_paid_by="vendor",
        return_methods=["post", "in_store"],
        returns_page_url="https://shop.example/returns",
        return_country="FR",
    )
    _html, document = graph_of(client)
    policy = _policy(document)
    assert policy["merchantReturnDays"] == 30
    assert policy["merchantReturnLink"] == "https://shop.example/returns"
    assert policy["returnPolicyCountry"] == "FR"
    assert policy["returnFees"] == "https://schema.org/FreeReturn"
    assert policy["returnMethod"] == [
        "https://schema.org/ReturnByMail",
        "https://schema.org/ReturnInStore",
    ]


def test_a_customer_paid_approved_policy_says_so(client: Client, theme: str) -> None:
    from apps.vendors.tests.factories import ReturnPolicyVersionFactory

    product = _sold_product()
    ReturnPolicyVersionFactory.create(
        vendor=product.vendor, status="approved", return_shipping_paid_by="customer"
    )
    _html, document = graph_of(client)
    assert _policy(document)["returnFees"] == "https://schema.org/ReturnShippingFees"


def test_a_pending_policy_is_not_stated(client: Client, theme: str) -> None:
    from apps.vendors.tests.factories import ReturnPolicyVersionFactory

    product = _sold_product()
    ReturnPolicyVersionFactory.create(
        vendor=product.vendor, status="pending", return_window_days=60
    )
    _html, document = graph_of(client)
    policy = _policy(document)
    assert policy["merchantReturnDays"] == 14
    assert policy["returnPolicyCountry"] == "DE"
    assert policy["returnFees"] == "https://schema.org/FreeReturn"  # the default, not the pending
    for absent in ("merchantReturnLink", "returnMethod"):
        assert absent not in policy


def test_a_vendor_without_a_country_falls_back_to_the_operator_country(
    client: Client, theme: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apps.seo import product_jsonld

    # TAX.12 cannot price a vendor without a country; the policy fallback is what is under test.
    monkeypatch.setattr(product_jsonld, "_offer_price", lambda page, net, request: net)
    _sold_product(country="")
    _html, document = graph_of(client)
    assert _policy(document)["returnPolicyCountry"] == "IT"  # the consumer_prices operator


def test_the_policy_read_is_one_query_per_page(client: Client, theme: str) -> None:
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from apps.vendors.tests.factories import ReturnPolicyVersionFactory

    product = _sold_product()
    for approved in (False, True):
        if approved:
            ReturnPolicyVersionFactory.create(vendor=product.vendor, status="approved")
        with CaptureQueriesContext(connection) as queries:
            client.get(MUG)
        reads = [q for q in queries if "vendors_returnpolicyversion" in q["sql"]]
        assert len(reads) == 1


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
    assert [n["@type"] for n in document["@graph"]] == [
        "Organization",
        "WebSite",
        "ItemPage",
        "Product",
        "BreadcrumbList",
    ]
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


# SEO.10 2/2: the vendor role in the brand and manufacturer references.


def _approved_brand(name: str = "Acme", *, with_maker: bool = False) -> Any:
    from apps.catalog.tests.factories import (
        BrandFactory,
        BrandTranslationFactory,
        ManufacturerFactory,
    )

    brand = BrandFactory.create(
        name=name, manufacturer=ManufacturerFactory.create() if with_maker else None
    )
    for language in configured_languages():
        BrandTranslationFactory.create(
            brand=brand,
            language=language,
            slug=f"{name.lower()}-{language}",
            status=TranslationStatus.PUBLISHED,
        )
    return brand


def _role_item(client: Client, **kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    Product.objects.all().delete()
    vendor = VendorFactory.create(
        status=Vendor.Status.ACTIVE,
        sells_online=True,
        legal_form=Vendor.LegalForm.SRL,
        default_product_role=kwargs.pop("default_role", ""),
    )
    live(price_net=Decimal("10.00"), gtin="4006381333931", vendor=vendor, **kwargs)
    _html, document = graph_of(client)
    return document, node(document, "Product")


def _org(document: dict[str, Any]) -> dict[str, Any]:
    org: dict[str, Any]
    [org] = [
        n
        for n in document["@graph"]
        if n["@type"] == "Organization" and n["@id"].startswith("http://localhost/shop/")
    ]
    return org


def test_a_producer_without_a_brand_is_the_brand_and_the_manufacturer(client: Client) -> None:
    document, item = _role_item(client, vendor_role="producer")
    org = _org(document)
    assert item["brand"] == {"@id": org["@id"]}
    assert item["manufacturer"] == {"@id": org["@id"]}
    assert item["offers"]["seller"] == {"@id": org["@id"]}
    assert schema_check.dangling_references(document) == []
    assert schema_check.rich_result_errors(document) == []


def test_a_producer_with_a_brand_keeps_the_brand_and_is_the_manufacturer(client: Client) -> None:
    brand = _approved_brand()
    document, item = _role_item(client, vendor_role="producer", brand=brand)
    assert item["brand"] == {"@id": node(document, "Brand")["@id"]}
    assert item["manufacturer"] == {"@id": _org(document)["@id"]}
    assert item["offers"]["seller"] == {"@id": _org(document)["@id"]}
    assert schema_check.dangling_references(document) == []


def test_a_cat20_manufacturer_wins_over_a_producer_vendor(client: Client) -> None:
    brand = _approved_brand(with_maker=True)
    document, item = _role_item(client, vendor_role="producer", brand=brand)
    [maker] = [n for n in document["@graph"] if "#manufacturer-" in n["@id"]]
    assert item["manufacturer"] == {"@id": maker["@id"]}
    assert schema_check.dangling_references(document) == []


def test_a_reseller_names_only_the_cat20_brand(client: Client) -> None:
    brand = _approved_brand()
    document, item = _role_item(client, vendor_role="reseller", brand=brand)
    assert item["brand"] == {"@id": node(document, "Brand")["@id"]}
    assert "manufacturer" not in item
    assert item["offers"]["seller"] == {"@id": _org(document)["@id"]}


def test_a_reseller_without_a_brand_makes_no_brand_claim(client: Client) -> None:
    document, item = _role_item(client, vendor_role="reseller")
    assert "brand" not in item
    assert "manufacturer" not in item
    assert item["offers"]["seller"] == {"@id": _org(document)["@id"]}


def test_a_reseller_with_a_cat20_manufacturer_names_it(client: Client) -> None:
    brand = _approved_brand(with_maker=True)
    document, item = _role_item(client, vendor_role="reseller", brand=brand)
    [maker] = [n for n in document["@graph"] if "#manufacturer-" in n["@id"]]
    assert item["manufacturer"] == {"@id": maker["@id"]}


def test_an_unknown_role_never_names_the_vendor_as_brand_or_manufacturer(client: Client) -> None:
    document, item = _role_item(client)
    assert "brand" not in item
    assert "manufacturer" not in item
    brand = _approved_brand()
    document, item = _role_item(client, brand=brand)
    assert item["brand"] == {"@id": node(document, "Brand")["@id"]}
    assert "manufacturer" not in item


def test_the_vendor_default_applies_and_the_product_override_wins(client: Client) -> None:
    _document, item = _role_item(client, default_role="producer")
    assert "manufacturer" in item
    _document, item = _role_item(client, default_role="producer", vendor_role="reseller")
    assert "manufacturer" not in item


def test_a_producer_and_a_reseller_of_one_gtin_share_the_brand_id(client: Client) -> None:
    brand = _approved_brand()
    _document, producer = _role_item(client, vendor_role="producer", brand=brand)
    _document, reseller = _role_item(client, vendor_role="reseller", brand=brand)
    assert producer["brand"] == reseller["brand"]


def test_a_person_seller_is_never_the_brand_or_manufacturer(client: Client) -> None:
    vendor = VendorFactory.create(
        status=Vendor.Status.ACTIVE,
        sells_online=True,
        italian_sole_proprietor=True,
        first_name="Mario",
        last_name="Rossi",
        legal_name="Mario Rossi",
        display_name="Mario Rossi",
        default_product_role="producer",
    )
    live(price_net=Decimal("10.00"), vendor=vendor)
    _html, document = graph_of(client)
    item = node(document, "Product")
    assert "brand" not in item
    assert "manufacturer" not in item
    assert not [n for n in document["@graph"] if n["@type"] == "Person"]


def test_an_on_request_producer_page_keeps_the_vendor_node_it_refers_to(client: Client) -> None:
    document, item = _role_item(
        client, vendor_role="producer", sales_mode=Product.SalesMode.ON_REQUEST
    )
    assert "offers" not in item
    assert item["brand"] == {"@id": _org(document)["@id"]}
    assert schema_check.dangling_references(document) == []


def test_the_vendor_role_adds_no_queries(client: Client) -> None:
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    brand = _approved_brand(with_maker=True)
    product = live(price_net=Decimal("10.00"), brand=brand)
    with CaptureQueriesContext(connection) as unknown:
        client.get(MUG)
    Product.objects.filter(pk=product.pk).update(vendor_role="producer")
    with CaptureQueriesContext(connection) as producer:
        client.get(MUG)
    assert len(producer) <= len(unknown)


def test_the_product_states_its_dimensions_material_colour_and_origin(client: Client) -> None:
    from apps.catalog.models import ProductTranslation

    product = live(
        price_net=Decimal("10.00"),
        length_mm=300,
        width_mm=200,
        height_mm=100,
        country_of_origin="IT",
    )
    ProductTranslation.objects.filter(product=product).update(
        material="Glazed ceramic", colour="Matt black"
    )
    _html, document = graph_of(client)
    item = node(document, "Product")
    assert item["width"] == {"@type": "QuantitativeValue", "value": 200, "unitCode": "MMT"}
    assert item["height"]["value"] == 100
    assert item["depth"] == {"@type": "QuantitativeValue", "value": 300, "unitCode": "MMT"}
    assert item["material"] == "Glazed ceramic"
    assert item["color"] == "Matt black"
    assert item["countryOfOrigin"] == {"@type": "Country", "name": "IT"}
    assert schema_check.dangling_references(document) == []


def test_a_product_without_those_fields_has_none_of_them(client: Client) -> None:
    live(price_net=Decimal("10.00"))
    _html, document = graph_of(client)
    item = node(document, "Product")
    for key in ("width", "height", "depth", "material", "color", "countryOfOrigin"):
        assert key not in item


def test_the_attributes_add_no_queries(client: Client) -> None:
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from apps.catalog.models import ProductTranslation

    product = live(price_net=Decimal("10.00"))
    with CaptureQueriesContext(connection) as bare:
        client.get(MUG)
    Product.objects.filter(pk=product.pk).update(
        length_mm=300, width_mm=200, height_mm=100, country_of_origin="IT"
    )
    ProductTranslation.objects.filter(product=product).update(material="Steel", colour="Red")
    with CaptureQueriesContext(connection) as full:
        client.get(MUG)
    assert len(full) <= len(bare)


def test_each_variant_carries_its_own_identifiers_and_image(client: Client) -> None:
    from apps.catalog.tests.factories import ProductImageFactory

    product = live(
        kind=Product.Kind.VARIABLE, price_net=Decimal("10.00"), gtin="4006381333931", mpn="MUG-77"
    )
    own = VariantFactory.create(
        product=product,
        gtin="96385074",
        mpn="MUG-77-B",
        cover_image=ProductImageFactory.create(product=product),
    )
    bare = VariantFactory.create(product=product, gtin="", mpn="")
    for variant in (own, bare):
        for language in configured_languages():
            VariantTranslationFactory.create(variant=variant, language=language, label="V")
    _html, document = graph_of(client)
    group = node(document, "ProductGroup")
    by_sku = {v["sku"]: v for v in group["hasVariant"]}
    assert by_sku[str(own.sku)]["gtin8"] == "96385074"
    assert by_sku[str(own.sku)]["mpn"] == "MUG-77-B"
    assert by_sku[str(own.sku)]["image"][0]["contentUrl"].startswith("http")
    # never the product's identifiers, and nothing blank
    for key in ("gtin8", "gtin13", "mpn", "image"):
        assert key not in by_sku[str(bare.sku)]


def _banded_method(product: Product, *, name: str = "Courier") -> Any:
    from apps.shipping.tests.factories import (
        ShippingMethodFactory,
        ShippingMethodTranslationFactory,
        ShippingZoneFactory,
        WeightBandFactory,
    )

    method = ShippingMethodFactory.create(
        vendor=product.vendor, zone=ShippingZoneFactory.create(countries=["IT", "DE"])
    )
    for language in configured_languages():
        ShippingMethodTranslationFactory.create(method=method, language=language, name=name)
    for low, high, cost in (("0", "1", "6.90"), ("1", "5", "9.90"), ("5", None, "14.90")):
        WeightBandFactory.create(
            method=method, from_kg=Decimal(low), to_kg=high and Decimal(high), cost_net=cost
        )
    return method


def test_each_shipping_node_names_its_service_and_other_weight_bands_follow(
    client: Client, theme: str
) -> None:
    product = live(price_net=Decimal("12.50"), weight_kg=Decimal("0.500"))
    _banded_method(product)
    _html, document = graph_of(client)
    nodes = [n for n in document["@graph"] if n["@type"] == "OfferShippingDetails"]
    assert len(nodes) == 3
    assert {n["name"] for n in nodes} == {"Courier"}
    own = [n for n in nodes if "hasShippingService" not in n]
    assert [n["shippingRate"]["value"] for n in own] == ["8.42"]
    banded = {
        n["hasShippingService"]["shippingConditions"]["weight"]["minValue"]: n
        for n in nodes
        if n not in own
    }
    assert banded["1.000"]["shippingRate"]["value"] == "12.08"  # 9.90 net + 22 % VAT
    assert "maxValue" not in banded["5.000"]["hasShippingService"]["shippingConditions"]["weight"]
    offer = node(document, "Product")["offers"]
    assert len(offer["shippingDetails"]) == 3
    assert schema_check.dangling_references(document) == []
    assert schema_check.rich_result_errors(document) == []


def test_the_banded_shipping_nodes_cost_no_query_per_band(client: Client, theme: str) -> None:
    product = live(price_net=Decimal("12.50"), weight_kg=Decimal("0.500"))
    _banded_method(product)
    client.get(MUG)  # warm caches
    with CaptureQueriesContext(connection) as one:
        client.get(MUG)
    from apps.shipping.tests.factories import WeightBandFactory

    method = product.vendor.shippingmethod_set.first()
    WeightBandFactory.create(method=method, from_kg=Decimal("20"), to_kg=None, cost_net="30")
    with CaptureQueriesContext(connection) as more:
        client.get(MUG)
    assert len(more) <= len(one)


def test_a_product_without_weight_gets_no_banded_nodes(client: Client, theme: str) -> None:
    product = live(price_net=Decimal("12.50"))
    _banded_method(product)
    _html, document = graph_of(client)
    assert [n for n in document["@graph"] if n["@type"] == "OfferShippingDetails"] == []
