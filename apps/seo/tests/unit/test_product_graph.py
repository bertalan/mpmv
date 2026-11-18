"""SEO.1: the Product part of the structured data graph, as pure functions (spec 10 §Structured
data graph, "Product node"). No database: plain data in, schema.org nodes out."""

import json
from datetime import date
from decimal import Decimal

import pytest

from apps.seo import product_graph as pg
from apps.seo import schema_check

SITE = "https://shop.example"
URL = f"{SITE}/product/mug/"
SELLER = f"{SITE}/shop/acme/#organization"
RETURNS = f"{SITE}/shop/acme/#return-policy"
SHIPPING = f"{SITE}/shop/acme/#shipping-eu"


def offer(**kwargs: object) -> pg.OfferData:
    base: dict[str, object] = {
        "price": Decimal("12.50"),
        "currency": "EUR",
        "availability": "in_stock",
        "url": URL,
        "seller_id": SELLER,
        "return_policy_id": RETURNS,
        "shipping_ids": (SHIPPING,),
    }
    base.update(kwargs)
    return pg.OfferData(**base)  # type: ignore[arg-type]


def product(**kwargs: object) -> pg.ProductData:
    base: dict[str, object] = {
        "url": URL,
        "name": "Mug",
        "sku": "ACME-1",
        "product_id": "6f1c",
        "images": (pg.ImageData(url=f"{SITE}/media/mug.jpg", width=800, height=600),),
        "offer": offer(),
    }
    base.update(kwargs)
    return pg.ProductData(**base)  # type: ignore[arg-type]


def test_the_product_node_has_stable_id_identifiers_and_the_vendor_sku() -> None:
    node = pg.product_node(
        product(gtin="4006381333931", mpn="MUG-77", brand_id=f"{SITE}/brand/x/#brand")
    )
    assert node["@type"] == "Product"
    assert node["@id"] == f"{URL}#product"
    assert node["sku"] == "ACME-1"
    assert node["gtin13"] == "4006381333931"
    assert node["mpn"] == "MUG-77"
    assert node["productID"] == "6f1c"
    assert node["brand"] == {"@id": f"{SITE}/brand/x/#brand"}
    assert "inLanguage" not in node


@pytest.mark.parametrize(
    ("gtin", "key"),
    [
        ("40170725", "gtin8"),
        ("036000291452", "gtin12"),
        ("4006381333931", "gtin13"),
        ("10012345678902", "gtin14"),
    ],
)
def test_the_gtin_property_follows_the_length(gtin: str, key: str) -> None:
    assert pg.product_node(product(gtin=gtin))[key] == gtin


def test_empty_or_malformed_identifiers_are_left_out() -> None:
    node = pg.product_node(product(gtin="12345", mpn="", brand_id=""))
    assert not {"gtin", "gtin8", "gtin12", "gtin13", "gtin14", "mpn", "brand"} & set(node)


def test_a_sold_product_has_an_offer_with_price_currency_and_availability() -> None:
    node = pg.product_node(product())
    offers = node["offers"]
    assert offers["@type"] == "Offer"
    assert offers["price"] == "12.50"
    assert offers["priceCurrency"] == "EUR"
    assert offers["availability"] == "https://schema.org/InStock"
    assert offers["itemCondition"] == "https://schema.org/NewCondition"
    assert offers["seller"] == {"@id": SELLER}
    assert offers["hasMerchantReturnPolicy"] == {"@id": RETURNS}
    assert offers["shippingDetails"] == [{"@id": SHIPPING}]
    assert "priceValidUntil" not in offers  # never a made-up date


@pytest.mark.parametrize(
    ("status", "schema"),
    [("in_stock", "InStock"), ("out_of_stock", "OutOfStock"), ("on_backorder", "BackOrder")],
)
def test_availability_comes_from_the_stock_status(status: str, schema: str) -> None:
    assert (
        pg.offer_node(offer(availability=status))["availability"] == f"https://schema.org/{schema}"
    )


def test_a_retired_product_is_discontinued() -> None:
    node = pg.product_node(product(offer=offer(availability="discontinued")))
    assert node["offers"]["availability"] == "https://schema.org/Discontinued"


def test_the_price_validity_comes_only_from_a_sale_end_date() -> None:
    node = pg.offer_node(offer(price_valid_until=date(2027, 1, 31)))
    assert node["priceValidUntil"] == "2027-01-31"


def test_the_vat_flag_is_stated_on_the_price_specification() -> None:
    gross = pg.offer_node(offer(price_includes_vat=True))["priceSpecification"]
    net = pg.offer_node(offer(price_includes_vat=False))["priceSpecification"]
    assert gross["valueAddedTaxIncluded"] is True
    assert net["valueAddedTaxIncluded"] is False
    assert gross["price"] == "12.50"
    assert gross["priceCurrency"] == "EUR"


@pytest.mark.parametrize("mode", ["on_request", "catalog_only"])
def test_a_product_not_sold_online_has_no_price_and_no_offer(mode: str) -> None:
    """Q-31, Q-33: no price in the JSON-LD of an on-request or catalogue-only product."""
    node = pg.product_node(
        product(offer=None, variants=(pg.VariantData(sku="V1", name="Red", url=URL),))
    )
    text = json.dumps(node)
    assert "offers" not in text
    assert "price" not in text.lower()
    assert mode  # both modes arrive here as "no offer"


def test_variants_make_a_product_group_with_one_offer_each() -> None:
    variants = (
        pg.VariantData(
            sku="V1",
            name="Mug red",
            url=URL,
            gtin="4006381333931",
            offer=offer(price=Decimal("10.00")),
        ),
        pg.VariantData(
            sku="V2",
            name="Mug blue",
            url=URL,
            offer=offer(price=Decimal("11.00"), availability="out_of_stock"),
        ),
    )
    node = pg.product_node(product(variants=variants, offer=None))
    assert node["@type"] == "ProductGroup"
    assert node["productGroupID"] == "ACME-1"
    assert [v["sku"] for v in node["hasVariant"]] == ["V1", "V2"]
    assert all(v["@type"] == "Product" for v in node["hasVariant"])
    assert node["hasVariant"][0]["gtin13"] == "4006381333931"
    assert node["hasVariant"][0]["offers"]["price"] == "10.00"
    assert node["hasVariant"][1]["offers"]["availability"] == "https://schema.org/OutOfStock"
    assert "offers" not in node  # the offers live on the variants


def test_a_variant_has_its_own_id_and_the_group_says_what_varies() -> None:
    variants = (
        pg.VariantData(sku="V 1", name="Mug red", gtin="4006381333931", mpn="M-R"),
        pg.VariantData(sku="V2", name="Mug blue", url=f"{URL}?v=V2"),
    )
    node = pg.product_node(
        product(variants=variants, offer=None, varies_by=("https://schema.org/color",))
    )
    assert node["variesBy"] == ["https://schema.org/color"]
    first, second = node["hasVariant"]
    assert first["@id"] == f"{URL}#variant-V%201"
    assert second["@id"] == f"{URL}#variant-V2"
    assert first["mpn"] == "M-R"
    assert "url" not in first  # the page cannot select a variant by URL
    assert second["url"] == f"{URL}?v=V2"


def test_images_carry_size_caption_and_licence_fields() -> None:
    image = pg.ImageData(
        url=f"{SITE}/media/a.jpg",
        width=800,
        height=600,
        caption="A mug",
        license="https://creativecommons.org/licenses/by/4.0/",
        acquire_license_page=f"{SITE}/licence/",
        credit_text="Ada",
        creator="Ada Lovelace",
        copyright_notice="(c) Ada",
    )
    node = pg.product_node(product(images=(image,)))
    [img] = node["image"]
    assert img["@type"] == "ImageObject"
    assert img["contentUrl"] == f"{SITE}/media/a.jpg"
    assert (img["width"], img["height"]) == (800, 600)
    assert img["caption"] == "A mug"
    assert img["license"].startswith("https://creativecommons.org")
    assert img["acquireLicensePage"] == f"{SITE}/licence/"
    assert img["creditText"] == "Ada"
    assert img["creator"] == {"@type": "Person", "name": "Ada Lovelace"}
    assert img["copyrightNotice"] == "(c) Ada"


def test_a_plain_image_has_only_what_it_knows() -> None:
    [img] = pg.product_node(product(images=(pg.ImageData(url=f"{SITE}/m.jpg"),)))["image"]
    assert set(img) == {"@type", "contentUrl", "url"}


def test_the_weight_is_a_quantitative_value_in_kilograms() -> None:
    weight = pg.product_node(product(weight_kg=Decimal("0.350")))["weight"]
    assert weight == {"@type": "QuantitativeValue", "value": 0.35, "unitCode": "KGM"}
    assert isinstance(weight["value"], float)


def test_the_breadcrumb_list_has_absolute_positions() -> None:
    crumbs = (
        pg.Crumb("Home", f"{SITE}/"),
        pg.Crumb("Mugs", f"{SITE}/product-category/mugs/"),
        pg.Crumb("Mug", URL),
    )
    node = pg.breadcrumb_node(page_url=URL, items=crumbs)
    assert node["@type"] == "BreadcrumbList"
    assert node["@id"] == f"{URL}#breadcrumb"
    assert [(e["position"], e["name"]) for e in node["itemListElement"]] == [
        (1, "Home"),
        (2, "Mugs"),
        (3, "Mug"),
    ]
    assert node["itemListElement"][1]["item"] == f"{SITE}/product-category/mugs/"


def test_the_seller_is_the_vendor_organization() -> None:
    node = pg.organization_node(node_id=SELLER, name="Acme", url=f"{SITE}/shop/acme/")
    assert node == {
        "@type": "Organization",
        "@id": SELLER,
        "name": "Acme",
        "url": f"{SITE}/shop/acme/",
    }


def test_the_return_policy_names_its_countries_and_window() -> None:
    node = pg.return_policy_node(
        node_id=RETURNS,
        countries=("IT", "DE"),
        return_country="IT",
        days=14,
        link=f"{SITE}/shop/acme/",
    )
    assert node["@type"] == "MerchantReturnPolicy"
    assert node["@id"] == RETURNS
    assert node["applicableCountry"] == ["IT", "DE"]
    assert node["returnPolicyCountry"] == "IT"
    assert node["returnPolicyCategory"] == "https://schema.org/MerchantReturnFiniteReturnWindow"
    assert node["merchantReturnDays"] == 14
    assert node["merchantReturnLink"] == f"{SITE}/shop/acme/"


def test_no_stated_payer_means_no_return_fees_but_a_defect_is_free() -> None:
    node = pg.return_policy_node(node_id=RETURNS, countries=("IT",), return_country="IT", days=14)
    assert "returnFees" not in node
    assert "merchantReturnLink" not in node
    assert "returnMethod" not in node
    assert node["itemDefectReturnFees"] == "https://schema.org/FreeReturn"
    assert node["merchantReturnDays"] == 14
    assert "returnShippingFeesAmount" not in node


def test_a_stated_payer_sets_the_return_fees_and_the_methods_map_to_schema_org() -> None:
    customer = pg.return_policy_node(
        node_id=RETURNS, countries=("IT",), return_country="IT", days=30, paid_by="customer"
    )
    assert customer["returnFees"] == "https://schema.org/ReturnShippingFees"
    shop = pg.return_policy_node(
        node_id=RETURNS,
        countries=("IT",),
        return_country="IT",
        days=30,
        paid_by="vendor",
        methods=("post", "drop_off", "in_store"),
    )
    assert shop["returnFees"] == "https://schema.org/FreeReturn"
    assert shop["returnMethod"] == [
        "https://schema.org/ReturnByMail",
        "https://schema.org/ReturnAtKiosk",
        "https://schema.org/ReturnInStore",
    ]


def test_shipping_details_group_the_countries_in_one_region_per_rate() -> None:
    node = pg.shipping_node(
        node_id=SHIPPING,
        countries=("IT", "DE", "FR"),
        rate=Decimal("6.90"),
        currency="EUR",
        min_days=2,
        max_days=5,
    )
    assert node["@type"] == "OfferShippingDetails"
    assert node["shippingRate"] == {"@type": "MonetaryAmount", "value": "6.90", "currency": "EUR"}
    assert node["shippingDestination"] == {
        "@type": "DefinedRegion",
        "addressCountry": ["IT", "DE", "FR"],
    }
    assert node["deliveryTime"]["transitTime"] == {
        "@type": "QuantitativeValue",
        "minValue": 2,
        "maxValue": 5,
        "unitCode": "DAY",
    }


def test_the_world_zone_has_no_country_list() -> None:
    node = pg.shipping_node(node_id=SHIPPING, countries=(), rate=Decimal("20.00"), currency="EUR")
    assert "shippingDestination" not in node


def graph(**kwargs: object) -> dict[str, object]:
    base: dict[str, object] = {
        "product": product(),
        "breadcrumb": pg.breadcrumb_node(
            page_url=URL, items=(pg.Crumb("Home", f"{SITE}/"), pg.Crumb("Mug", URL))
        ),
        "seller": pg.organization_node(node_id=SELLER, name="Acme", url=f"{SITE}/shop/acme/"),
        "return_policy": pg.return_policy_node(
            node_id=RETURNS, countries=("IT",), return_country="IT", days=14
        ),
        "shipping": [
            pg.shipping_node(
                node_id=SHIPPING, countries=("IT",), rate=Decimal("5.00"), currency="EUR"
            )
        ],
    }
    base.update(kwargs)
    return pg.product_graph(**base)  # type: ignore[arg-type]


def test_the_graph_is_one_document_with_every_reference_resolving() -> None:
    document = graph()
    assert document["@context"] == "https://schema.org"
    assert schema_check.dangling_references(document) == []
    assert schema_check.rich_result_errors(document) == []


def test_the_schema_check_covers_specification_seller_breadcrumb_and_variants() -> None:
    variants = (pg.VariantData(sku="V1", name="Mug red", offer=offer()),)
    document = graph(product=product(variants=variants, offer=None, varies_by=("size",)))
    assert schema_check.rich_result_errors(document) == []
    item = document["@graph"][0]  # type: ignore[index]
    variant = item["hasVariant"][0]
    variant["offers"]["priceSpecification"]["price"] = "1.00"
    del variant["offers"]["seller"]
    del variant["@id"]
    del item["variesBy"]
    del document["@graph"][1]["itemListElement"][0]["item"]  # type: ignore[index]
    errors = " ".join(schema_check.rich_result_errors(document))
    for expected in ("priceSpecification", "@id", "variesBy", "item URL"):
        assert expected in errors
    assert "seller" not in errors  # SEO.15: an omitted seller (a natural person) is no error


def test_the_schema_check_finds_a_missing_price_and_a_dangling_reference() -> None:
    document = graph(shipping=[])
    assert schema_check.dangling_references(document) == [SHIPPING]
    document = graph()
    del document["@graph"][0]["offers"]["price"]  # type: ignore[index]
    assert any("price" in error for error in schema_check.rich_result_errors(document))


def test_an_on_request_graph_passes_the_check_without_offers() -> None:
    document = graph(product=product(offer=None), seller=None, return_policy=None, shipping=[])
    assert schema_check.rich_result_errors(document) == []
    assert "price" not in json.dumps(document).lower()


def test_the_text_cannot_close_the_script_element() -> None:
    hostile = "</script><img src=x onerror=alert(1)>"
    text = pg.graph_text(graph(product=product(name=hostile, description=hostile)))
    assert not {"<", ">", "&"} & set(text)
    assert json.loads(text)["@graph"][0]["name"] == hostile


def test_the_product_graph_stays_under_the_budget() -> None:
    many = tuple(
        pg.ImageData(url=f"{SITE}/media/{i}.jpg", width=800, height=600, caption="x" * 80)
        for i in range(6)
    )
    assert len(pg.graph_text(graph(product=product(images=many))).encode()) < 30 * 1024
