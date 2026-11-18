"""SEO.15: a sole trader's shop name is the seller; a natural person's legal name never is.

Both the product JSON-LD (SEO.1) and the page graph (SEO.7, storefront) follow the one rule, and
every reference to an omitted seller is dropped with it.
"""

import json
import re
from decimal import Decimal
from typing import Any

import pytest
from django.apps import apps
from django.test import Client
from wagtail.models import Site

from apps.catalog.models import Product
from apps.catalog.tests.factories import ProductFactory, ProductTranslationFactory
from apps.core.i18n import configured_languages, primary_language
from apps.core.models import TranslationStatus
from apps.seo import schema_check, seller
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory
from tests.i18n import product_url, vendor_url

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("consumer_prices")]

SCRIPT = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)


@pytest.fixture(autouse=True)
def _site_allows_indexing() -> None:
    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )


def sole_trader(display_name: str | None = None) -> Vendor:
    vendor = VendorFactory.create(
        status=Vendor.Status.ACTIVE,
        sells_online=True,
        italian_sole_proprietor=True,
        first_name="Mario",
        last_name="Rossi",
        legal_name="Mario Rossi",
        display_name=display_name or "Mario Rossi",
    )
    return vendor


def sell(vendor: Vendor) -> str:
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
    return product_url(primary_language(), f"mug-{primary_language()}")


def graph_of(client: Client, path: str) -> dict[str, Any]:
    response = client.get(path)
    assert response.status_code == 200, path
    blocks = SCRIPT.findall(response.content.decode())
    assert len(blocks) == 1
    document: dict[str, Any] = json.loads(blocks[0])
    assert schema_check.dangling_references(document) == []
    return document


def organizations(document: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        n
        for n in document["@graph"]
        if n["@type"] == "Organization" and "#organization" in n["@id"]
    ]


def vendor_orgs(document: dict[str, Any]) -> list[dict[str, Any]]:
    return [n for n in organizations(document) if n["@id"] != "http://localhost/#organization"]


def test_a_sole_trader_with_a_shop_name_emits_the_shop_name(client: Client) -> None:
    vendor = sole_trader("Rossi Ceramics")
    document = graph_of(client, sell(vendor))
    [org] = vendor_orgs(document)
    assert org["name"] == "Rossi Ceramics"
    [product] = [n for n in document["@graph"] if n["@type"] == "Product"]
    assert product["offers"]["seller"] == {"@id": org["@id"]}
    errors = schema_check.rich_result_errors(document, person_names=seller.person_names(vendor))
    assert errors == []


def test_a_sole_trader_with_only_the_legal_name_has_no_seller_in_the_product_graph(
    client: Client,
) -> None:
    vendor = sole_trader()
    document = graph_of(client, sell(vendor))
    assert vendor_orgs(document) == []
    [product] = [n for n in document["@graph"] if n["@type"] == "Product"]
    assert "seller" not in product["offers"]
    assert "Mario Rossi" not in json.dumps(document)
    assert schema_check.rich_result_errors(document, person_names=seller.person_names(vendor)) == []


def test_the_storefront_of_such_a_sole_trader_has_no_organization(client: Client) -> None:
    vendor = sole_trader()
    sell(vendor)
    document = graph_of(client, vendor_url(primary_language(), vendor.slug))
    assert vendor_orgs(document) == []
    [web] = [n for n in document["@graph"] if n["@type"] in {"ProfilePage", "CollectionPage"}]
    assert web["@type"] == "CollectionPage"
    assert "mainEntity" not in web or web["mainEntity"] != {"@id": f"{web['url']}#organization"}
    assert not [
        n
        for n in document["@graph"]
        if "#organization" in n["@id"]
        and n["@type"] == "Organization"
        and n["@id"] != "http://localhost/#organization"
    ]
    assert schema_check.rich_result_errors(document, person_names=seller.person_names(vendor)) == []


def test_the_storefront_of_a_sole_trader_with_a_shop_name_is_a_profile_page(
    client: Client,
) -> None:
    vendor = sole_trader("Rossi Ceramics")
    sell(vendor)
    document = graph_of(client, vendor_url(primary_language(), vendor.slug))
    [org] = vendor_orgs(document)
    assert org["name"] == "Rossi Ceramics"
    [web] = [n for n in document["@graph"] if n["@type"] == "ProfilePage"]
    assert web["mainEntity"] == {"@id": org["@id"]}


def test_a_company_keeps_its_name(client: Client) -> None:
    vendor = VendorFactory.create(
        status=Vendor.Status.ACTIVE,
        sells_online=True,
        italian_company=True,
        legal_name="Rossi Ceramics S.r.l.",
        display_name="Rossi Ceramics S.r.l.",
    )
    document = graph_of(client, sell(vendor))
    [org] = vendor_orgs(document)
    assert org["name"] == "Rossi Ceramics S.r.l."


# --- Search results (SEO.7 part 2): vendor hits are Organization items too


def search_graph(client: Client, query: str) -> dict[str, Any]:
    from tests.i18n import language_prefix

    return graph_of(client, f"{language_prefix(primary_language())}/?s={query}")


def search_items(document: dict[str, Any]) -> list[dict[str, Any]]:
    [items] = [n for n in document["@graph"] if n["@type"] == "ItemList"]
    elements: list[dict[str, Any]] = items["itemListElement"]
    return elements


def test_a_sole_trader_with_only_the_legal_name_is_not_a_search_result_item(
    client: Client,
) -> None:
    person = sole_trader("Mario Rossi")
    company = VendorFactory.create(
        status=Vendor.Status.ACTIVE,
        italian_company=True,
        legal_name="Rossi Ceramics S.r.l.",
        display_name="Rossi Ceramics S.r.l.",
    )
    document = search_graph(client, "rossi")
    elements = search_items(document)
    assert [e["item"]["name"] for e in elements] == ["Rossi Ceramics S.r.l."]
    assert [e["item"]["@type"] for e in elements] == ["Organization"]
    assert [e["position"] for e in elements] == [1]
    assert "Mario Rossi" not in json.dumps(document)
    people = seller.person_names(person) | seller.person_names(company)
    assert schema_check.rich_result_errors(document, person_names=people) == []
    # The visible results list is unchanged: the shop is still shown to visitors.
    html = client.get("/?s=rossi").content.decode()
    assert "Mario Rossi" in html


def test_search_positions_stay_consecutive_when_a_vendor_in_the_middle_is_dropped(
    client: Client,
) -> None:
    sole_trader("Zeta Mario Rossi")  # legal name differs: shown as its shop name
    VendorFactory.create(
        status=Vendor.Status.ACTIVE, italian_company=True, display_name="Alpha Rossi S.r.l."
    )
    person = sole_trader("Mario Rossi")
    VendorFactory.create(
        status=Vendor.Status.ACTIVE, italian_company=True, display_name="Omega Rossi S.r.l."
    )
    document = search_graph(client, "rossi")
    elements = search_items(document)
    names = [e["item"]["name"] for e in elements]
    assert "Mario Rossi" not in names
    assert names == ["Alpha Rossi S.r.l.", "Omega Rossi S.r.l.", "Zeta Mario Rossi"]
    assert [e["position"] for e in elements] == [1, 2, 3]
    assert schema_check.rich_result_errors(document, person_names=seller.person_names(person)) == []


def test_a_sole_trader_with_a_shop_name_is_a_search_result_item(client: Client) -> None:
    sole_trader("Rossi Ceramics")
    elements = search_items(search_graph(client, "ceramics"))
    assert [e["item"]["name"] for e in elements] == ["Rossi Ceramics"]
