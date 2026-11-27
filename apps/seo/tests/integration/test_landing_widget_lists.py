"""SEO.7 (slice 1 of the blog and vendor lists): the lists a vendor story page and a term page
show (the vendor's featured products, the term's related articles) feed the page graph as named
`ItemList`s, public items in the page language only, without offers (spec 10, "Widgets feed the
graph")."""

import json
import re
from typing import Any

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import translation

from apps.catalog.models import Product, Term
from apps.catalog.tests.factories import ProductFactory, ProductTranslationFactory
from apps.cms.services import SeedReport, seed_site
from apps.cms.tests.factories import StandardPageFactory
from apps.cms.tests.integration.test_landing_pages import (  # noqa: F401
    _about,
    _home,
    _translate,
    listed,
    maker,
    term_landing,
    vendor_landing,
)
from apps.core.i18n import primary_language
from apps.core.models import TranslationStatus
from apps.seo import schema_check
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory
from tests.i18n import language_prefix, other_language

pytestmark = pytest.mark.django_db


@pytest.fixture
def seeded(db: None) -> SeedReport:
    """The sample home, About page and menus in every configured language."""
    return seed_site()


SCRIPT = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)


def graph_of(path: str) -> list[dict[str, Any]]:
    response = Client().get(path)
    assert response.status_code == 200
    blocks = SCRIPT.findall(response.content.decode())
    assert len(blocks) == 1
    document = json.loads(blocks[0])
    assert schema_check.dangling_references(document) == []
    graph: list[dict[str, Any]] = document["@graph"]
    return graph


def lists(graph: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [n for n in graph if n["@type"] == "ItemList"]


def test_the_vendor_story_lists_its_featured_products(seeded: SeedReport) -> None:
    acme = VendorFactory.create(status=Vendor.Status.ACTIVE, slug="acme", display_name="Acme")
    bag = listed("Acme bag", vendor=acme)
    listed("Acme draft", vendor=acme, status=Product.Status.DRAFT)
    page = vendor_landing(acme)
    [node] = lists(graph_of(page.url))
    assert node["name"]
    assert node["@id"].startswith("http")
    assert node["@id"].endswith("#featured")
    assert node["numberOfItems"] == len(node["itemListElement"]) == 1
    [element] = node["itemListElement"]
    assert element["position"] == 1
    assert element["item"]["@id"] == element["item"]["url"]
    assert "offers" not in element["item"]
    assert element["item"]["name"] == bag.translations.get(language=primary_language()).title


def test_a_vendor_story_without_featured_products_has_no_list(seeded: SeedReport) -> None:
    acme = VendorFactory.create(status=Vendor.Status.ACTIVE, slug="acme", display_name="Acme")
    listed("Acme bag", vendor=acme)
    page = vendor_landing(acme, featured_products=0)
    assert lists(graph_of(page.url)) == []


def test_a_retired_product_leaves_the_vendor_story_list(seeded: SeedReport) -> None:
    acme = VendorFactory.create(status=Vendor.Status.ACTIVE, slug="acme", display_name="Acme")
    product = listed("Acme bag", vendor=acme)
    page = vendor_landing(acme)
    assert len(lists(graph_of(page.url))) == 1
    Product.objects.filter(pk=product.pk).update(status=Product.Status.RETIRED)
    assert lists(graph_of(page.url)) == []


def test_the_term_page_lists_its_related_articles(seeded: SeedReport, maker: Term) -> None:  # noqa: F811
    about = _about()
    term_landing(maker, related=[("page", {"page": about, "label": "", "text": ""})])
    graph = graph_of("/compatibility/maker/")
    [related] = [n for n in lists(graph) if n["@id"].endswith("#related")]
    assert related["name"]
    [element] = related["itemListElement"]
    assert element["position"] == 1
    assert element["item"]["url"].endswith(about.url)
    assert "offers" not in element["item"]
    ids = [n["@id"] for n in lists(graph)]
    assert len(ids) == len(set(ids))


def test_a_term_page_without_related_articles_has_no_related_list(
    seeded: SeedReport,
    maker: Term,  # noqa: F811
) -> None:
    term_landing(maker)
    graph = graph_of("/compatibility/maker/")
    assert not [n for n in lists(graph) if n["@id"].endswith("#related")]


def test_the_story_list_adds_a_constant_number_of_queries(seeded: SeedReport) -> None:
    acme = VendorFactory.create(status=Vendor.Status.ACTIVE, slug="acme", display_name="Acme")
    listed("Acme one", vendor=acme)
    page = vendor_landing(acme)
    Client().get(page.url)
    with CaptureQueriesContext(connection) as few:
        Client().get(page.url)
    for number in range(4):
        listed(f"Acme more {number}", vendor=acme)
    with CaptureQueriesContext(connection) as many:
        Client().get(page.url)
    assert len(many) == len(few)


def _gettext(message: str, language: str) -> str:
    with translation.override(language):
        return str(translation.gettext(message))


def _page_node(graph: list[dict[str, Any]]) -> dict[str, Any]:
    [node] = [n for n in graph if n["@type"] in {"WebPage", "ProfilePage", "CollectionPage"}]
    return node


def test_the_vendor_story_list_is_in_the_page_language_only(seeded: SeedReport) -> None:
    other = other_language()
    acme = VendorFactory.create(status=Vendor.Status.ACTIVE, slug="acme", display_name="Acme")
    both = listed("Acme bag", vendor=acme)
    only_primary = ProductFactory.create(vendor=acme, status=Product.Status.PUBLISHED)
    ProductTranslationFactory.create(
        product=only_primary,
        language=primary_language(),
        title="Primary only",
        slug="primary-only",
        status=TranslationStatus.PUBLISHED,
    )
    page = vendor_landing(acme)
    copy = _translate(page, other, slug="il-laboratorio")
    copy.refresh_from_db()
    graph = graph_of(copy.url)
    [node] = lists(graph)
    assert node["name"] == _gettext("Featured products", other)
    assert node["name"] != _gettext("Featured products", primary_language())
    web = _page_node(graph)
    assert web["inLanguage"] == other
    assert web["url"].startswith(f"http://localhost{language_prefix(other)}/")
    assert node["@id"] == f"{web['url']}#featured"
    names = [e["item"]["name"] for e in node["itemListElement"]]
    assert names == [both.translations.get(language=other).title]
    assert all(
        e["item"]["url"].startswith(f"http://localhost{language_prefix(other)}/")
        for e in node["itemListElement"]
    )


def test_the_term_page_related_list_is_in_the_page_language_only(
    seeded: SeedReport,
    maker: Term,  # noqa: F811
) -> None:
    other = other_language()
    about_other = _about(other)
    cards = [
        ("page", {"page": _about(), "label": "", "text": ""}),  # primary-language page only
        ("page", {"page": about_other, "label": "", "text": ""}),
    ]
    page = term_landing(maker, related=cards)
    copy = _translate(page, other, related=cards)
    copy.refresh_from_db()
    graph = graph_of(copy.url)
    [related] = [n for n in lists(graph) if n["@id"].endswith("#related")]
    assert related["name"] == _gettext("Related articles", other)
    assert related["name"] != _gettext("Related articles", primary_language())
    web = _page_node(graph)
    assert web["inLanguage"] == other
    assert related["@id"].startswith(f"http://localhost{language_prefix(other)}/")
    [element] = related["itemListElement"]
    assert element["item"]["url"].endswith(about_other.url)


def test_the_story_of_a_suspended_vendor_has_no_page_and_no_list(seeded: SeedReport) -> None:
    # The other leak cases (a closed vendor) use the same gate: `is_public()` (CMS.4 test
    # `test_the_page_of_a_vendor_no_longer_active_is_gone`).
    acme = VendorFactory.create(status=Vendor.Status.ACTIVE, slug="acme", display_name="Acme")
    listed("Acme bag", vendor=acme)
    page = vendor_landing(acme)
    url = page.url
    assert len(lists(graph_of(url))) == 1
    Vendor.objects.filter(pk=acme.pk).update(status=Vendor.Status.SUSPENDED)
    response = Client().get(url)
    assert response.status_code == 404
    assert b"#featured" not in response.content


def test_the_related_list_adds_no_queries_per_card(
    seeded: SeedReport,
    maker: Term,  # noqa: F811
) -> None:
    first = ("page", {"page": _about(), "label": "", "text": ""})
    page = term_landing(maker, related=[first])
    Client().get("/compatibility/maker/")
    with CaptureQueriesContext(connection) as few:
        Client().get("/compatibility/maker/")
    more = [
        ("page", {"page": StandardPageFactory.create(title=f"Guide {n}"), "label": "", "text": ""})
        for n in range(4)
    ]
    page.related = [first, *more]
    page.save_revision().publish()
    with CaptureQueriesContext(connection) as many:
        response = Client().get("/compatibility/maker/")
    assert response.status_code == 200
    related = [n for n in lists(graph_of("/compatibility/maker/")) if n["@id"].endswith("#related")]
    assert related[0]["numberOfItems"] == 5
    assert len(many) == len(few)
