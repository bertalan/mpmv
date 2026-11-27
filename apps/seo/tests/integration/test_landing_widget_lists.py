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

from apps.catalog.models import Product, Term
from apps.cms.services import SeedReport, seed_site
from apps.cms.tests.integration.test_landing_pages import (  # noqa: F401
    _about,
    _home,
    listed,
    maker,
    term_landing,
    vendor_landing,
)
from apps.seo import schema_check
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

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
    assert element["item"]["name"] == bag.translations.get(language="en").title


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
    assert len(many) <= len(few)
