"""SEO.7 part 2: `WebSite` + `SearchAction`, `CollectionPage` + `ItemList` on the listings, the
events index and the blog index, and the search results page as a `SearchResultsPage`.

Every graph is the page's one script, every `@id` reference resolves, every node and list item
is in the page language (no cross-language fallback), and listing graphs cost no query per item.
"""

import datetime as dt
import json
import re
from typing import Any

import pytest
from django.apps import apps
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from wagtail.models import Page, Site

from apps.catalog.models import Product
from apps.catalog.tests.factories import (
    CategoryFactory,
    CategoryTranslationFactory,
    ProductFactory,
    ProductTranslationFactory,
)
from apps.cms.tests.factories import BlogIndexPageFactory, BlogPostPageFactory
from apps.core.i18n import primary_language
from apps.core.models import TranslationStatus
from apps.events.tests.factories import EventPageFactory
from apps.seo import schema_check
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory
from tests.i18n import language_prefix, other_language, product_url, vendor_url

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("consumer_prices")]

SCRIPT = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
PUBLISHED = TranslationStatus.PUBLISHED


@pytest.fixture(autouse=True)
def _site(consumer_prices: None) -> None:
    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )


def graph_of(path: str, *, robots: str = "") -> list[dict[str, Any]]:
    response = Client().get(path)
    assert response.status_code == 200, path
    html = response.content.decode()
    blocks = SCRIPT.findall(html)
    assert len(blocks) == 1, f"{path}: one JSON-LD script, got {len(blocks)}"
    if robots:
        assert f'content="{robots}"' in html, path
    document = json.loads(blocks[0])
    assert schema_check.dangling_references(document) == [], path
    assert schema_check.rich_result_errors(document) == [], path
    graph: list[dict[str, Any]] = document["@graph"]
    return graph


def of(graph: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    [found] = [node for node in graph if node["@type"] == kind]
    return found


def item_lists(graph: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [node for node in graph if node["@type"] == "ItemList"]


def listed_product(
    title: str, *, language: str | None = None, vendor: Vendor | None = None, **kwargs: Any
) -> Product:
    language = language or primary_language()
    product = ProductFactory.create(
        vendor=vendor or VendorFactory.create(status=Vendor.Status.ACTIVE),
        status=Product.Status.PUBLISHED,
        **kwargs,
    )
    ProductTranslationFactory.create(
        product=product,
        language=language,
        title=title,
        slug=title.lower().replace(" ", "-"),
        status=PUBLISHED,
    )
    return product


def published(page: Page) -> Page:
    page.save_revision().publish()
    page.refresh_from_db()
    return page


def category_path(category: Any, language: str) -> str:
    from apps.catalog import services

    path = services.category_url(category, language)
    assert path
    return str(path)


@pytest.fixture
def category() -> Any:
    found = CategoryFactory.create()
    CategoryTranslationFactory.create(
        category=found, language=primary_language(), status=PUBLISHED, name="Glazes", slug="glazes"
    )
    return found


# --- WebSite + SearchAction


@pytest.mark.parametrize("language", [primary_language(), other_language()])
def test_website_has_a_search_action_pointing_at_the_language_home(language: str) -> None:
    from apps.catalog import services

    path = f"{language_prefix(language)}/"
    graph = graph_of(path)
    action = of(graph, "WebSite")["potentialAction"]
    assert action["@type"] == "SearchAction"
    assert action["target"]["@type"] == "EntryPoint"
    template = action["target"]["urlTemplate"]
    assert template == f"http://localhost{path}?{services.SEARCH_PARAMETER}={{search_term_string}}"
    assert action["query-input"] == "required name=search_term_string"


# --- Listings


def test_category_listing_is_a_collection_page_with_the_products_of_the_page(
    category: Any,
) -> None:
    for title in ("Red Glaze", "Blue Glaze", "Green Glaze"):
        listed_product(title, category=category)
    language = primary_language()
    graph = graph_of(category_path(category, language))
    web = of(graph, "CollectionPage")
    [items] = item_lists(graph)
    assert web["mainEntity"] == {"@id": items["@id"]}
    assert web["inLanguage"] == items["inLanguage"] == language
    assert items["numberOfItems"] == 3
    elements = items["itemListElement"]
    assert [e["position"] for e in elements] == [1, 2, 3]
    assert {e["item"]["name"] for e in elements} == {"Red Glaze", "Blue Glaze", "Green Glaze"}
    for element in elements:
        item = element["item"]
        assert item["@type"] == "Product"
        assert item["@id"] == item["url"]
        assert item["url"] == f"http://localhost{product_url(language, item['url'].split('/')[-2])}"
        assert "offers" not in item


def test_listing_items_are_the_current_page_only(category: Any, settings: Any) -> None:
    from apps.catalog import services

    for number in range(services.LISTING_PER_PAGE + 2):
        listed_product(f"Glaze {number:02d}", category=category)
    path = category_path(category, primary_language())
    first = item_lists(graph_of(path))[0]
    assert first["numberOfItems"] == services.LISTING_PER_PAGE
    second = item_lists(graph_of(f"{path}?page=2"))[0]
    assert second["numberOfItems"] == 2
    assert not {e["item"]["@id"] for e in first["itemListElement"]} & {
        e["item"]["@id"] for e in second["itemListElement"]
    }


def test_a_product_without_a_text_in_the_page_language_is_not_listed_as_another_language(
    category: Any,
) -> None:
    CategoryTranslationFactory.create(
        category=category, language=other_language(), status=PUBLISHED, name="X", slug="x-cat"
    )
    listed_product("Only primary", category=category)
    path = category_path(category, other_language())
    graph = graph_of(path)
    for items in item_lists(graph):
        for element in items["itemListElement"]:
            assert element["item"]["url"].startswith(
                f"http://localhost{language_prefix(other_language())}/"
            )


def test_vendor_storefront_is_a_profile_page_with_its_products() -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    listed_product("Storefront mug", vendor=vendor)
    path = vendor_url(primary_language(), vendor.slug)
    graph = graph_of(path)
    web = of(graph, "ProfilePage")
    organization = [
        n for n in graph if n["@type"] == "Organization" and "#organization" in n["@id"]
    ]
    shop = [n for n in organization if n["@id"] == f"http://localhost{path}#organization"]
    assert len(shop) == 1
    assert web["mainEntity"] == {"@id": shop[0]["@id"]}
    [items] = item_lists(graph)
    assert {"@id": items["@id"]} in web["hasPart"]
    assert items["itemListElement"][0]["item"]["name"] == "Storefront mug"


def test_a_retired_or_unlisted_product_is_not_in_the_list(category: Any) -> None:
    listed_product("Visible", category=category)
    hidden = listed_product("Draft", category=category)
    Product.objects.filter(pk=hidden.pk).update(status=Product.Status.DRAFT)
    [items] = item_lists(graph_of(category_path(category, primary_language())))
    assert [e["item"]["name"] for e in items["itemListElement"]] == ["Visible"]


def test_an_empty_listing_has_no_item_list(category: Any) -> None:
    graph = graph_of(category_path(category, primary_language()))
    assert item_lists(graph) == []
    assert "mainEntity" not in of(graph, "CollectionPage")


def test_listing_graph_queries_do_not_grow_with_the_products(category: Any) -> None:
    path = category_path(category, primary_language())
    listed_product("First", category=category)
    Client().get(path)  # warm caches (content types, site settings)
    with CaptureQueriesContext(connection) as few:
        Client().get(path)
    for number in range(8):
        listed_product(f"More {number}", category=category)
    with CaptureQueriesContext(connection) as many:
        Client().get(path)
    assert len(many) <= len(few) + 1, (len(few), len(many))


# --- Events and blog index


def test_events_index_is_a_collection_page_with_its_events() -> None:
    start = dt.datetime.now(dt.UTC) + dt.timedelta(days=10)
    event = EventPageFactory.create(
        title="Spring Fair", starts_at=start, ends_at=start + dt.timedelta(hours=3)
    )
    published(event)
    index = event.get_parent()
    graph = graph_of(index.url)
    web = of(graph, "CollectionPage")
    [items] = item_lists(graph)
    assert web["mainEntity"] == {"@id": items["@id"]}
    [element] = items["itemListElement"]
    assert element["item"]["name"] == "Spring Fair"
    assert element["item"]["@type"] == "Event"
    assert element["item"]["@id"].startswith("http")
    assert element["item"]["url"].endswith(event.url)


def test_blog_index_lists_its_posts() -> None:
    blog = published(BlogIndexPageFactory.create())
    published(BlogPostPageFactory.create(parent=blog, title="Glazing basics"))
    graph = graph_of(blog.url)
    [items] = item_lists(graph)
    assert of(graph, "CollectionPage")["mainEntity"] == {"@id": items["@id"]}
    assert items["itemListElement"][0]["item"]["name"] == "Glazing basics"
    assert items["itemListElement"][0]["item"]["@type"] == "BlogPosting"


# --- Search results


def search_path(query: str, language: str | None = None) -> str:
    return f"{language_prefix(language or primary_language())}/?s={query}"


def test_search_results_page_is_a_search_results_page_with_mixed_results() -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE, display_name="Zephyr Pottery")
    listed_product("Zephyr mug", vendor=vendor)
    start = dt.datetime.now(dt.UTC) + dt.timedelta(days=10)
    published(
        EventPageFactory.create(
            title="Zephyr fair", starts_at=start, ends_at=start + dt.timedelta(hours=2)
        )
    )
    blog = published(BlogIndexPageFactory.create())
    published(BlogPostPageFactory.create(parent=blog, title="Zephyr glazing"))
    graph = graph_of(search_path("zephyr"), robots="noindex, follow")
    web = of(graph, "SearchResultsPage")
    assert web["inLanguage"] == primary_language()
    assert web["isPartOf"] == {"@id": of(graph, "WebSite")["@id"]}
    [items] = item_lists(graph)
    assert web["mainEntity"] == {"@id": items["@id"]}
    kinds = [e["item"]["@type"] for e in items["itemListElement"]]
    assert kinds == ["Product", "Event", "BlogPosting", "Organization"]
    assert [e["position"] for e in items["itemListElement"]] == [1, 2, 3, 4]
    names = [e["item"]["name"] for e in items["itemListElement"]]
    assert names == ["Zephyr mug", "Zephyr fair", "Zephyr glazing", "Zephyr Pottery"]
    for element in items["itemListElement"]:
        assert element["item"]["@id"] == element["item"]["url"]
        assert element["item"]["url"].startswith("http://localhost")


def test_search_with_no_match_or_no_query_has_no_item_list() -> None:
    for path in (search_path("nothingmatchesthis"), search_path("")):
        graph = graph_of(path, robots="noindex, follow")
        assert of(graph, "SearchResultsPage")
        assert item_lists(graph) == []


def test_search_results_do_not_borrow_another_language() -> None:
    listed_product("Quokka mug", language=primary_language())
    graph = graph_of(search_path("quokka", other_language()), robots="noindex, follow")
    assert item_lists(graph) == []
    assert of(graph, "SearchResultsPage")["inLanguage"] == other_language()


def test_a_hostile_query_is_inert_in_the_search_graph() -> None:
    hostile = "%3C/script%3E%3Cimg%20src=x%20onerror=alert(1)%3E"
    response = Client().get(search_path(hostile))
    html = response.content.decode()
    assert "<img src=x" not in html
    assert len(SCRIPT.findall(html)) == 1
