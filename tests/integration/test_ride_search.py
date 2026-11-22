"""THM.17: the `ride` public search results page (CAT.27) on Bootstrap 5.3.

The page is rendered with the `ride` theme: the page banner with the only h1, the search form on
Bootstrap form classes, products as Bootstrap cards, the other groups as Bootstrap list groups
with "see all" links, numbered Bootstrap pagination, no stylesheet of the app's minimal layout.
A parity test compares what the page says (names, links, headings, controls, robots) with the
base theme's page on a query with results, an empty query and a query with no match.
"""

import re
from decimal import Decimal
from typing import Any

import pytest
from bs4 import BeautifulSoup
from django.apps import apps
from django.test import Client
from pytest_django.fixtures import Settings as SettingsWrapper
from wagtail.models import Site

from apps.catalog import services
from apps.catalog.models import Product
from apps.catalog.tests.factories import (
    CategoryFactory,
    CategoryTranslationFactory,
    ProductFactory,
    ProductTranslationFactory,
)
from apps.cms.services import seed_site
from apps.cms.tests.factories import BlogPostPageFactory
from apps.core.i18n import primary_language
from apps.core.models import TranslationStatus
from apps.events.tests.factories import EventPageFactory
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory
from tests.i18n import translated

pytestmark = pytest.mark.django_db

PUBLISHED = TranslationStatus.PUBLISHED


@pytest.fixture(autouse=True)
def _content(monkeypatch: pytest.MonkeyPatch, settings: SettingsWrapper) -> None:
    seed_site()
    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )
    settings.SEARCH_RATE_LIMIT = 1000
    monkeypatch.setattr(services, "LISTING_PER_PAGE", 2)
    category = CategoryFactory.create()
    CategoryTranslationFactory.create(
        category=category,
        language=primary_language(),
        name="Gaskets",
        slug="gaskets",
        status=PUBLISHED,
    )
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE, sells_online=True)
    for index in range(3):
        product = ProductFactory.create(
            vendor=vendor,
            category=category,
            status=Product.Status.PUBLISHED,
            price_net=Decimal("10.00"),
        )
        ProductTranslationFactory.create(
            product=product,
            language=primary_language(),
            title=f"Mango bag {index}",
            slug=f"mango-bag-{index}",
            short_description="",
            description="",
            status=PUBLISHED,
        )
    EventPageFactory.create(title="Mango fair", live=True)
    BlogPostPageFactory.create(title="Mango tuning guide")
    VendorFactory.create(
        status=Vendor.Status.ACTIVE, display_name="Mango Works", slug="mango-works"
    )


def page(client: Client, query: dict[str, str]) -> Any:
    response = client.get("/", query)
    assert response.status_code == 200
    return BeautifulSoup(response.content, "html.parser")


def ride_page(client: Client, settings: SettingsWrapper, query: dict[str, str]) -> Any:
    settings.MPMV_THEME_DEFAULT = "ride"
    return page(client, query)


def test_the_banner_holds_the_only_h1_and_the_form_is_a_search_landmark(
    client: Client, settings: SettingsWrapper
) -> None:
    soup = ride_page(client, settings, {"s": "mango"})
    assert [h.get_text(strip=True) for h in soup.find_all("h1")] == [translated("Search")]
    assert soup.select_one(".page-banner h1")
    form = soup.select_one(".search-page form[role=search][method=get]")
    assert form is not None
    field = form.select_one("input.form-control[name=s]")
    assert field is not None
    assert field["value"] == "mango"
    assert soup.find("label", attrs={"for": field["id"]})
    submit = form.select_one("button[type=submit].btn")
    assert submit is not None


def test_ride_does_not_load_the_apps_minimal_stylesheets(
    client: Client, settings: SettingsWrapper
) -> None:
    soup = ride_page(client, settings, {"s": "mango"})
    assert not [
        link for link in soup.find_all("link", rel="stylesheet") if "catalog/" in link["href"]
    ]
    assert soup.find("style") is None
    assert soup.find(attrs={"style": True}) is None
    assert all(script.get("src") or script.get("type") for script in soup.find_all("script"))


def test_products_are_bootstrap_cards_and_groups_are_list_groups(
    client: Client, settings: SettingsWrapper
) -> None:
    soup = ride_page(client, settings, {"s": "mango"})
    assert len(soup.select("ul.product-grid .card.product-card")) == 2
    groups = soup.select("section.search-group")
    assert groups
    for group in groups:
        assert group.select_one("ul.list-group a")
        assert group.select_one("h2[id^=search-group-]")
    assert soup.select_one("ul.pagination li.page-item.active a.page-link[aria-current=page]")


def test_the_empty_query_shows_the_form_only(client: Client, settings: SettingsWrapper) -> None:
    soup = ride_page(client, settings, {"s": ""})
    assert soup.select_one("input.form-control[name=s]")
    assert not soup.select(".product-card, .search-group, .pagination")


def test_no_match_gives_suggestions_as_a_list_group(
    client: Client, settings: SettingsWrapper
) -> None:
    soup = ride_page(client, settings, {"s": "qqqqzzzz"})
    box = soup.select_one(".search-suggestions")
    assert box is not None
    assert box.select_one("ul.list-group a")
    assert "Gaskets" in box.get_text()


def search_data(
    client: Client, settings: SettingsWrapper, theme: str, query: dict[str, str]
) -> Any:
    """What the page says, not how it is marked up."""
    settings.MPMV_THEME_DEFAULT = theme
    soup = page(client, query)
    main = soup.find("main") or soup
    form = main.select_one("form[role=search]")
    assert form is not None
    robots = soup.find("meta", attrs={"name": "robots"})
    return {
        "title": soup.title.get_text(strip=True) if soup.title else None,
        "h1": [h.get_text(strip=True) for h in soup.find_all("h1")],
        "form": (form["action"], form["method"]),
        "controls": sorted(
            (c["name"], c.get("type", c.name), c.get("value", ""))
            for c in main.select("form input[name], form select[name]")
        ),
        "labels": [label.get_text(strip=True) for label in main.select("form label")],
        "h2": [(h.get("id"), h.get_text(strip=True)) for h in main.select("h2")],
        "products": [
            (a["href"], a.get_text(strip=True)) for a in main.select(".product-card h3 a")
        ],
        "prices": [p.get_text(" ", strip=True) for p in main.select(".product-card-price")],
        "group_links": [
            (a["href"], a.get("lang"), a.get_text(strip=True))
            for a in main.select("section.search-group a")
        ],
        "summaries": [p.get_text(strip=True) for p in main.select(".search-group-items p")],
        "suggestions": [
            (a["href"], a.get_text(strip=True)) for a in main.select(".search-suggestions a")
        ],
        "pagination": [
            (a["href"], a.get("rel", [None])[0], a.has_attr("aria-current"), a.get_text(strip=True))
            for a in main.select("nav[aria-label=Pagination] a")
        ],
        "back": [a["href"] for a in main.find_all("a", string=re.compile("Back to all"))],
        "status": [p.get_text(strip=True) for p in main.select("[role=status]")],
        "robots": robots["content"] if robots else None,
        "prev": [link["href"] for link in soup.find_all("link", rel="prev")],
        "next": [link["href"] for link in soup.find_all("link", rel="next")],
    }


@pytest.mark.parametrize(
    "query",
    [
        {"s": "mango"},
        {"s": "mango", "page": "2"},
        {"s": "mango", "in": "events"},
        {"s": "mango", "price_max": "20"},
        {"s": ""},
        {"s": "qqqqzzzz"},
    ],
    ids=["results", "paged", "one-group", "filtered", "empty", "no-match"],
)
def test_base_and_ride_search_pages_carry_the_same_data(
    client: Client, settings: SettingsWrapper, query: dict[str, str]
) -> None:
    base = search_data(client, settings, "base", query)
    ride = search_data(client, settings, "ride", query)
    assert ride == base
    assert base["robots"] == "noindex, follow"
    if query["s"] == "mango" and "in" not in query:
        assert base["products"]
        assert base["group_links"]
    if query["s"] == "qqqqzzzz":
        assert base["suggestions"]
