"""SEO.7: the product page's widgets (related products, other sellers) feed the page graph as
named `ItemList`s of product ids, linked from the `ItemPage` by `hasPart`, without offers."""

import json
import re
from typing import Any

import pytest
from django.apps import apps
from django.test import Client
from wagtail.models import Site

from apps.catalog.models import Product
from apps.catalog.tests.factories import (
    ProductFactory,
    ProductTranslationFactory,
    TermFactory,
    TermTranslationFactory,
)
from apps.core.i18n import primary_language
from apps.core.models import TranslationStatus
from apps.seo import schema_check
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory
from tests.i18n import language_prefix, other_language, product_url

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("consumer_prices")]

SCRIPT = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
PUBLISHED = TranslationStatus.PUBLISHED


@pytest.fixture(autouse=True)
def _site() -> None:
    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )


def make(slug: str, *, language: str | None = None, **kwargs: Any) -> Product:
    product = ProductFactory.create(
        vendor=kwargs.pop("vendor", None) or VendorFactory.create(status=Vendor.Status.ACTIVE),
        status=Product.Status.PUBLISHED,
        **kwargs,
    )
    ProductTranslationFactory.create(
        product=product,
        language=language or primary_language(),
        title=slug.title(),
        slug=slug,
        status=PUBLISHED,
    )
    return product


def graph_of(slug: str, language: str | None = None) -> list[dict[str, Any]]:
    language = language or primary_language()
    response = Client().get(product_url(language, slug))
    assert response.status_code == 200
    blocks = SCRIPT.findall(response.content.decode())
    assert len(blocks) == 1
    document = json.loads(blocks[0])
    assert schema_check.dangling_references(document) == []
    graph: list[dict[str, Any]] = document["@graph"]
    return graph


def lists(graph: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [n for n in graph if n["@type"] == "ItemList"]


def page_of(graph: list[dict[str, Any]]) -> dict[str, Any]:
    [page] = [n for n in graph if n["@type"] == "ItemPage"]
    return page


def test_related_products_are_a_named_list_linked_from_the_page() -> None:
    term = TermFactory.create()
    TermTranslationFactory.create(term=term, language=primary_language(), status=PUBLISHED)
    main = make("main-item")
    others = [make(f"sibling-{n}") for n in range(2)]
    for product in (main, *others):
        product.terms.add(term)
    graph = graph_of("main-item")
    [related] = lists(graph)
    assert related["name"]
    assert related["inLanguage"] == primary_language()
    assert related["numberOfItems"] == 2
    urls = {e["item"]["url"] for e in related["itemListElement"]}
    assert urls == {
        f"http://localhost{product_url(primary_language(), p.translations.get().slug)}"
        for p in others
    }
    assert all("offers" not in e["item"] for e in related["itemListElement"])
    assert {"@id": related["@id"]} in page_of(graph)["hasPart"]


def test_other_sellers_are_a_second_list_with_a_distinct_id() -> None:
    term = TermFactory.create()
    TermTranslationFactory.create(term=term, language=primary_language(), status=PUBLISHED)
    main = make("main-item", gtin="4006381333931")
    main.terms.add(term)
    rival = make("rival-item", gtin="4006381333931")
    rival.terms.add(term)
    graph = graph_of("main-item")
    found = lists(graph)
    assert len(found) == 2
    assert len({n["@id"] for n in found}) == 2
    parts = page_of(graph)["hasPart"]
    assert all({"@id": n["@id"]} in parts for n in found)
    assert all(n["numberOfItems"] == 1 for n in found)


def test_a_product_without_widgets_has_no_list() -> None:
    make("lonely-item")
    assert lists(graph_of("lonely-item")) == []


def test_a_retired_product_leaves_the_lists() -> None:
    term = TermFactory.create()
    TermTranslationFactory.create(term=term, language=primary_language(), status=PUBLISHED)
    main = make("main-item")
    gone = make("gone-item")
    for product in (main, gone):
        product.terms.add(term)
    Product.objects.filter(pk=gone.pk).update(status=Product.Status.RETIRED)
    assert lists(graph_of("main-item")) == []


def test_other_language_items_are_not_listed() -> None:
    term = TermFactory.create()
    TermTranslationFactory.create(term=term, language=primary_language(), status=PUBLISHED)
    main = make("main-item")
    foreign = make("foreign-item", language=other_language())
    for product in (main, foreign):
        product.terms.add(term)
    for node in lists(graph_of("main-item")):
        for element in node["itemListElement"]:
            assert element["item"]["url"].startswith(
                f"http://localhost{language_prefix(primary_language())}/"
            )
