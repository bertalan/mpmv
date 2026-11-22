"""THM.7 part 1: the `ride` listing pages on Bootstrap 5.3 (spec 14 §14.6, §14.9).

A term page is rendered with the `ride` theme: the page banner with the only h1 and the
breadcrumb, the filter form on Bootstrap form classes, product cards as Bootstrap cards and the
numbered pagination as Bootstrap `.pagination`. No inline script or style, no stylesheet of the
app's minimal listing layout.
"""

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
    ProductFactory,
    ProductTranslationFactory,
    TaxonomyFactory,
    TaxonomyTranslationFactory,
    TermFactory,
    TermTranslationFactory,
)
from apps.core.models import TranslationStatus
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory
from tests.i18n import translated

pytestmark = pytest.mark.django_db

PUBLISHED = TranslationStatus.PUBLISHED


@pytest.fixture
def term_page(monkeypatch: pytest.MonkeyPatch, settings: SettingsWrapper) -> str:
    """A term "maker" with three products, one per page."""
    settings.MPMV_THEME_DEFAULT = "ride"
    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )
    monkeypatch.setattr(services, "LISTING_PER_PAGE", 1)
    taxonomy = TaxonomyFactory.create(code="compat", has_landing=True, filterable=False)
    TaxonomyTranslationFactory.create(
        taxonomy=taxonomy, name="Compatibility", slug="compatibility", status=PUBLISHED
    )
    maker = TermFactory.create(taxonomy=taxonomy)
    TermTranslationFactory.create(term=maker, name="Maker", slug="maker", status=PUBLISHED)
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE, sells_online=True)
    for index in range(3):
        product = ProductFactory.create(
            vendor=vendor, status=Product.Status.PUBLISHED, price_net=Decimal("10.00")
        )
        ProductTranslationFactory.create(
            product=product, title=f"Bag {index}", slug=f"bag-{index}", status=PUBLISHED
        )
        product.terms.add(maker)
    return "/compatibility/maker/"


def page(client: Client, path: str) -> Any:
    response = client.get(path)
    assert response.status_code == 200
    return BeautifulSoup(response.content, "html.parser")


def test_the_banner_holds_the_only_h1_and_the_breadcrumb(client: Client, term_page: str) -> None:
    soup = page(client, term_page)
    banner = soup.select_one(".page-banner")
    assert banner is not None
    assert [h.get_text(strip=True) for h in soup.find_all("h1")] == ["Maker"]
    assert banner.h1 is not None
    nav = banner.select_one("nav.breadcrumb")
    assert nav is not None
    assert nav["aria-label"] == translated("Breadcrumb")
    assert nav.select_one("[aria-current=page]").get_text(strip=True) == "Maker"
    assert nav.find("a")["href"]  # Home


def test_the_listing_does_not_load_the_apps_minimal_stylesheet(
    client: Client, term_page: str
) -> None:
    soup = page(client, term_page)
    assert not [
        link for link in soup.find_all("link", rel="stylesheet") if "catalog/" in link["href"]
    ]
    assert soup.find("style") is None
    assert soup.find(attrs={"style": True}) is None
    assert all(script.get("src") or script.get("type") for script in soup.find_all("script"))


def test_products_are_bootstrap_cards_in_a_grid_with_one_link_each(
    client: Client, term_page: str
) -> None:
    soup = page(client, term_page)
    cards = soup.select(".row .card.product-card")
    assert len(cards) == 1
    assert len(cards[0].select("h3 a")) == 1
    assert "Bag" in cards[0].h3.get_text()
    assert cards[0].select_one(".product-card-price")  # the hook of CAT.8 stays


def test_the_filter_form_uses_bootstrap_controls_with_labels(
    client: Client, term_page: str
) -> None:
    soup = page(client, term_page)
    form = soup.select_one("form[aria-labelledby=listing-filters-heading]")
    assert form is not None
    assert form["method"] == "get"
    assert form.select("input.form-control")
    for field in form.select("input.form-control, select.form-select, input.form-check-input"):
        assert soup.find("label", attrs={"for": field["id"]}), field["id"]
    submit = form.select_one("button[type=submit]")
    assert submit is not None
    assert "btn" in submit["class"]


def test_the_pagination_is_a_bootstrap_pagination_with_numbers(
    client: Client, term_page: str
) -> None:
    soup = page(client, term_page + "?page=2")
    nav = soup.select_one(f'nav[aria-label="{translated("Pagination")}"]')
    assert nav is not None
    assert nav.select_one("ul.pagination")
    items = nav.select("li.page-item > a.page-link")
    assert [a.get_text(strip=True) for a in items if a.get_text(strip=True).isdigit()] == [
        "1",
        "2",
        "3",
    ]
    current = nav.select("li.page-item.active > a[aria-current=page]")
    assert [a.get_text(strip=True) for a in current] == ["2"]
    assert nav.select_one("a[rel=prev]")
    assert nav.select_one("a[rel=next]")


def test_the_empty_state_offers_the_way_back(client: Client, term_page: str) -> None:
    soup = page(client, term_page + "?price_min=900")
    assert soup.select_one(".listing-empty a")


def listing_data(
    client: Client, path: str, theme: str, settings: SettingsWrapper
) -> dict[str, Any]:
    """What a listing says, not how it is marked up: controls, links, prices, robots, rel links."""
    settings.MPMV_THEME_DEFAULT = theme
    soup = page(client, path)
    form = soup.select_one("form[aria-labelledby=listing-filters-heading]")
    assert form is not None
    robots = soup.find("meta", attrs={"name": "robots"})
    return {
        "form_action": form["action"],
        "controls": sorted(
            (c["name"], c.get("type", c.name), c.get("value", ""), c.has_attr("checked"))
            for c in form.select("input[name], select[name]")
        ),
        "chips": [(a["href"], a.get_text(" ", strip=True)) for a in soup.select(".filter-chips a")],
        "pagination": [
            (a["href"], a.get("rel", [None])[0], a.has_attr("aria-current"))
            for a in soup.select(f'nav[aria-label="{translated("Pagination")}"] a')
        ],
        "prices": [p.get_text(" ", strip=True) for p in soup.select(".product-card-price")],
        "titles": [a.get_text(strip=True) for a in soup.select(".product-card h3 a")],
        "robots": robots["content"] if robots else None,
        "prev": [link["href"] for link in soup.find_all("link", rel="prev")],
        "next": [link["href"] for link in soup.find_all("link", rel="next")],
    }


@pytest.mark.parametrize(
    "query",
    ["", "?page=2", "?price_min=5&price_max=50", "?price_min=5&page=2"],
    ids=["first", "middle", "filtered", "filtered-paged"],
)
def test_base_and_ride_listings_carry_the_same_data(
    client: Client, term_page: str, settings: SettingsWrapper, query: str
) -> None:
    base = listing_data(client, term_page + query, "base", settings)
    ride = listing_data(client, term_page + query, "ride", settings)
    assert ride == base
    assert base["prices"]  # the comparison is not vacuous


def test_a_filtered_listing_carries_chips_and_filters_across_pages(
    client: Client, term_page: str, settings: SettingsWrapper
) -> None:
    for theme in ("base", "ride"):
        data = listing_data(client, term_page + "?price_min=5&page=2", theme, settings)
        assert data["chips"][0][1].startswith(
            translated("From %(currency)s %(amount)s").split("%")[0]
        )
        assert data["chips"][-1][1] == translated("Clear all filters")
        assert data["pagination"]
        assert all("price_min=5" in href for href, _, _ in data["pagination"])
        assert ("price_min", "number", "5", False) in data["controls"]


def test_a_filtered_listing_with_no_products_is_noindex_in_both_themes(
    client: Client, term_page: str, settings: SettingsWrapper
) -> None:
    data = {
        t: listing_data(client, term_page + "?price_min=900", t, settings) for t in ("base", "ride")
    }
    assert data["ride"] == data["base"]
    assert data["base"]["robots"] == "noindex, follow"
    assert data["base"]["chips"]
