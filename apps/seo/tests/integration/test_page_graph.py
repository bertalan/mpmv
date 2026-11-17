"""SEO.7 (part 1): `seo.page_graph`, the one JSON-LD block of every public page.

Coverage per page type (snapshot of the node types), one script per page, every `@id` reference
resolves, no cross-language fallback, an inert hostile product name, and a sweep that fails on a
public page type with no registered builder.
"""

import datetime as dt
import json
import re
from typing import Any

import pytest
from django.apps import apps
from django.template.loader import render_to_string
from django.test import Client, RequestFactory
from wagtail.models import Page, Site, get_page_models

from apps.catalog.tests.factories import BrandFactory, BrandTranslationFactory, CategoryFactory
from apps.cms.tests.factories import (
    BlogAuthorFactory,
    BlogIndexPageFactory,
    BlogPostPageFactory,
    StandardPageFactory,
)
from apps.core.i18n import primary_language
from apps.core.tests.factories import PlatformOperatorFactory
from apps.events.tests.factories import EventPageFactory
from apps.seo import page_graph, schema_check
from apps.themes import services as themes

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("consumer_prices")]

SCRIPT = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)


@pytest.fixture(autouse=True)
def _site() -> None:
    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )
    PlatformOperatorFactory.create(
        open_ended=True,
        valid_from=dt.datetime(2020, 1, 1, tzinfo=dt.UTC),
        legal_name="Operator Legal Name Srl",
    )


def graph_of(path: str) -> list[dict[str, Any]]:
    response = Client().get(path)
    assert response.status_code == 200, path
    blocks = SCRIPT.findall(response.content.decode())
    assert len(blocks) == 1, f"{path}: a page has exactly one JSON-LD script, got {len(blocks)}"
    document = json.loads(blocks[0])
    assert document["@context"] == "https://schema.org"
    assert schema_check.dangling_references(document) == [], path
    assert schema_check.rich_result_errors(document) == [], path
    graph: list[dict[str, Any]] = document["@graph"]
    return graph


def types(graph: list[dict[str, Any]]) -> list[str]:
    return [str(node["@type"]) for node in graph]


def of(graph: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    [found] = [node for node in graph if node["@type"] == kind]
    return found


def published(page: Page) -> Page:
    page.save_revision().publish()
    page.refresh_from_db()
    return page


def test_home_page_graph() -> None:
    graph = graph_of("/")
    assert types(graph) == ["Organization", "WebSite", "WebPage"]
    assert of(graph, "Organization")["name"] == "Operator Legal Name Srl"
    assert of(graph, "WebSite")["publisher"] == {"@id": of(graph, "Organization")["@id"]}
    assert of(graph, "WebPage")["isPartOf"] == {"@id": of(graph, "WebSite")["@id"]}


def test_standard_page_graph_has_a_breadcrumb_and_the_page_language() -> None:
    page = published(StandardPageFactory.create(title="Shipping info"))
    graph = graph_of(page.url)
    assert types(graph) == ["Organization", "WebSite", "WebPage", "BreadcrumbList"]
    web = of(graph, "WebPage")
    assert web["name"] == "Shipping info"
    assert web["inLanguage"] == primary_language()
    assert web["breadcrumb"] == {"@id": of(graph, "BreadcrumbList")["@id"]}
    crumbs = of(graph, "BreadcrumbList")["itemListElement"]
    assert [c["name"] for c in crumbs][-1] == "Shipping info"
    assert crumbs[-1]["item"].startswith("http")


def test_faq_blocks_join_the_page_graph_not_a_second_script() -> None:
    page = StandardPageFactory.create(
        title="Help",
        body=[("faq", {"items": [{"question": "Do you ship?", "answer": "<p>Yes.</p>"}]})],
    )
    published(page)
    graph = graph_of(page.url)
    assert of(graph, "FAQPage")["mainEntity"][0]["name"] == "Do you ship?"
    assert {"@id": of(graph, "FAQPage")["@id"]} in of(graph, "WebPage")["hasPart"]


def test_blog_post_and_author_graphs() -> None:
    blog = published(BlogIndexPageFactory.create())
    author = BlogAuthorFactory.create(name="Ada Example", slug="ada-example")
    post = published(BlogPostPageFactory.create(parent=blog, author=author, title="Glazing"))
    graph = graph_of(post.url)
    assert types(graph) == [
        "Organization", "WebSite", "WebPage", "BreadcrumbList", "BlogPosting", "Person",
    ]  # fmt: skip
    posting = of(graph, "BlogPosting")
    assert posting["author"]["@id"] == of(graph, "Person")["@id"]
    assert posting["publisher"] == {"@id": of(graph, "Organization")["@id"]}
    assert posting["inLanguage"] == primary_language()
    assert of(graph, "WebPage")["mainEntity"] == {"@id": posting["@id"]}

    author_graph = graph_of(f"{blog.url}author/ada-example/")
    assert "ProfilePage" in types(author_graph)
    assert of(author_graph, "Person")["name"] == "Ada Example"
    assert of(author_graph, "ProfilePage")["mainEntity"] == {
        "@id": of(author_graph, "Person")["@id"]
    }


def test_blog_index_is_a_collection_page() -> None:
    blog = published(BlogIndexPageFactory.create())
    assert "CollectionPage" in types(graph_of(blog.url))


def test_event_page_graph() -> None:
    event = EventPageFactory.create(
        starts_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=10),
        ends_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=10, hours=3),
    )
    published(event)
    graph = graph_of(event.url)
    assert {"Organization", "WebSite", "WebPage", "BreadcrumbList"} <= set(types(graph))


def test_category_and_brand_listings_are_collection_pages() -> None:
    category = CategoryFactory.create()
    from apps.catalog import services

    path = services.category_url(category, primary_language())
    if path:
        assert of(graph_of(path), "CollectionPage")["inLanguage"] == primary_language()
    brand = BrandFactory.create(name="Acme", status="approved")
    BrandTranslationFactory.create(
        brand=brand, language=primary_language(), slug="acme", status="published"
    )
    graph = graph_of(services.brand_page_paths(brand)[primary_language()])
    assert "CollectionPage" in types(graph)
    assert "Brand" in types(graph)


def test_the_graph_is_the_same_under_the_ride_theme(settings: Any) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    graph = graph_of("/")
    assert types(graph) == ["Organization", "WebSite", "WebPage"]


def test_ride_layout_renders_one_block() -> None:
    request = RequestFactory().get("/")
    with themes.use_theme("ride"):
        html = render_to_string("layouts/base.html", {"request": request})
    assert len(SCRIPT.findall(html)) <= 1


def test_no_graph_without_an_operator() -> None:
    apps.get_model("core", "PlatformOperator").objects.all().delete()
    blocks = SCRIPT.findall(Client().get("/").content.decode())
    assert len(blocks) == 1
    graph = json.loads(blocks[0])["@graph"]
    assert "Organization" not in types(graph)
    assert schema_check.dangling_references(json.loads(blocks[0])) == []


# --- The sweep


def test_every_public_page_model_has_a_graph_builder_or_an_exemption() -> None:
    """A new public page type fails here until it registers a builder (or states why it has no
    graph)."""
    project = [
        model
        for model in get_page_models()
        if model.__module__.startswith("apps.") and not model._meta.abstract
    ]
    assert project
    missing = [m.__name__ for m in project if not page_graph.covers(m)]
    assert missing == [], f"page types without a graph builder: {missing}"


def test_the_registered_view_types_are_covered() -> None:
    names = {t.__name__ for t in page_graph.registered_types()}
    assert {"ProductPage", "ListingAnchor"} <= names


def test_exempt_types_state_a_reason() -> None:
    assert all(reason.strip() for reason in page_graph.exemptions().values())


# --- Hostile input


def test_a_hostile_product_name_is_inert_in_the_one_block() -> None:
    from apps.catalog.models import Product
    from apps.catalog.tests.factories import ProductFactory, ProductTranslationFactory
    from apps.core.i18n import configured_languages
    from apps.core.models import TranslationStatus
    from apps.vendors.models import Vendor
    from apps.vendors.tests.factories import VendorFactory

    hostile = "</script><img src=x onerror=alert(1)>"
    product = ProductFactory.create(
        vendor=VendorFactory.create(status=Vendor.Status.ACTIVE, sells_online=True),
        status=Product.Status.PUBLISHED,
    )
    for language in configured_languages():
        ProductTranslationFactory.create(
            product=product,
            language=language,
            title=hostile,
            slug=f"mug-{language}",
            status=TranslationStatus.PUBLISHED,
        )
    html = Client().get("/product/mug-en/").content.decode()
    assert "<img src=x" not in html
    [block] = SCRIPT.findall(html)
    assert hostile in [n.get("name") for n in json.loads(block)["@graph"]]
    assert "Product" in types(json.loads(block)["@graph"])
