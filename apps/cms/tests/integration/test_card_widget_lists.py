"""SEO.7 2/2: the featured links block and the strip feed the page graph as named `ItemList`s of
the pages their cards lead to (pages, terms, vendors, products), linked from the page by `hasPart`.
"""

import json
import re
from typing import Any

import pytest
from django.test import Client, RequestFactory

from apps.cms.blocks import page_graph_nodes
from apps.cms.models import HomePage
from apps.cms.services import SeedReport
from apps.cms.tests.integration.test_product_grid import _about, _home
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

SCRIPT = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)


def _featured(*cards: tuple[str, Any], heading: str = "Worth a look") -> tuple[str, Any]:
    return ("featured", {"heading": heading, "cards": [(kind, {kind: t}) for kind, t in cards]})


def _strip(*cards: tuple[str, Any], heading: str = "From us") -> tuple[str, Any]:
    items: list[tuple[str, Any]] = [(k, {k: t}) for k, t in cards]
    items.append(("testimonial", {"quote": "Quote", "author": "Ada"}))
    return ("strip", {"heading": heading, "cards": items})


def _publish(home: HomePage, body: list[tuple[str, Any]]) -> HomePage:
    home.body = body
    home.save_revision().publish()
    home.refresh_from_db()
    return home


def _nodes(home: HomePage) -> list[dict[str, Any]]:
    request = RequestFactory().get(home.url)
    return page_graph_nodes(home.body, {"request": request, "page": home})


def test_featured_links_give_a_named_list_of_the_pages_the_cards_lead_to(
    seeded: SeedReport,
) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    home = _publish(_home(), [_featured(("page", _about()), ("vendor", vendor))])
    [node] = _nodes(home)
    assert node["@type"] == "ItemList"
    assert node["name"] == "Worth a look"
    assert node["numberOfItems"] == len(node["itemListElement"]) >= 1
    for position, element in enumerate(node["itemListElement"], 1):
        assert element["position"] == position
        assert element["item"]["@id"] == element["item"]["url"]
        assert "offers" not in element["item"]


def test_a_strip_lists_its_cards_but_not_its_testimonials(seeded: SeedReport) -> None:
    home = _publish(_home(), [_strip(("page", _about()))])
    [node] = _nodes(home)
    assert node["name"] == "From us"
    assert node["numberOfItems"] == 1


def test_a_block_with_no_public_card_gives_no_list(seeded: SeedReport) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.SUSPENDED)
    home = _publish(_home(), [_featured(("vendor", vendor))])
    assert _nodes(home) == []


def test_the_rendered_page_links_each_list_once_with_a_distinct_id(seeded: SeedReport) -> None:
    home = _publish(
        _home(), [_featured(("page", _about())), _strip(("page", _about()), heading="Again")]
    )
    html = Client().get(home.url).content.decode()
    blocks = SCRIPT.findall(html)
    assert len(blocks) == 1
    graph = json.loads(blocks[0])["@graph"]
    lists = [n for n in graph if n["@type"] == "ItemList"]
    assert len(lists) == 2
    assert len({n["@id"] for n in lists}) == 2
    [page] = [n for n in graph if n["@type"] == "WebPage"]
    assert all({"@id": n["@id"]} in page["hasPart"] for n in lists)
