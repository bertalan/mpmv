"""CAT.8 (part 3/3): what a listing page renders (spec 03 §3.2, D-07, Q-31).

A listing page has its name as the only h1, a plain GET filter form that works without
JavaScript (checkboxes per taxonomy term, price range, vendor), chips for the applied filters
whose remove links are canonical URLs on the page's current URL, product cards (title link,
public price or "Price on request"), a pagination nav and an empty state. No inline script or
style: the CSP has no 'unsafe-inline'.
"""

import re
from decimal import Decimal
from typing import Any

import pytest
from bs4 import BeautifulSoup
from django.apps import apps
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from wagtail.models import Site

from apps.catalog import services
from apps.catalog.models import Product, SalesMode
from apps.catalog.tests.factories import (
    CategoryFactory,
    CategoryTranslationFactory,
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

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _site_allows_indexing() -> None:
    """These tests are about the listing rules: the site has turned its indexing switch on."""
    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )


PUBLISHED = TranslationStatus.PUBLISHED


def listed(title: str, **kwargs: Any) -> Product:
    kwargs.setdefault(
        "vendor", VendorFactory.create(status=Vendor.Status.ACTIVE, sells_online=True)
    )
    kwargs.setdefault("status", Product.Status.PUBLISHED)
    product = ProductFactory.create(**kwargs)
    slug = title.lower().replace(" ", "-")
    ProductTranslationFactory.create(
        product=product, title=title, slug=f"{slug}-en", status=PUBLISHED
    )
    ProductTranslationFactory.create(
        product=product, language="it", title=f"{title} IT", slug=f"{slug}-it", status=PUBLISHED
    )
    return product


@pytest.fixture
def shop() -> dict[str, Any]:
    """A landing taxonomy "compatibility" with a "maker" term, a filterable "colour" taxonomy
    with "red" and "blue", and two products on the maker: one for sale, one on request."""
    compat = TaxonomyFactory.create(code="compat", has_landing=True, filterable=False)
    TaxonomyTranslationFactory.create(
        taxonomy=compat, name="Compatibility", slug="compatibility", status=PUBLISHED
    )
    TaxonomyTranslationFactory.create(
        taxonomy=compat, language="it", name="Compatibilità", slug="compatibilita",
        status=PUBLISHED,
    )  # fmt: skip
    maker = TermFactory.create(taxonomy=compat)
    TermTranslationFactory.create(term=maker, name="Maker", slug="maker", status=PUBLISHED)
    TermTranslationFactory.create(
        term=maker, language="it", name="Marca", slug="marca", status=PUBLISHED
    )
    colour = TaxonomyFactory.create(code="colour", hierarchical=False)
    TaxonomyTranslationFactory.create(
        taxonomy=colour, name="Colour", slug="colour", status=PUBLISHED
    )
    TaxonomyTranslationFactory.create(
        taxonomy=colour, language="it", name="Colore", slug="colore", status=PUBLISHED
    )
    red = TermFactory.create(taxonomy=colour)
    TermTranslationFactory.create(term=red, name="Red", slug="red", status=PUBLISHED)
    TermTranslationFactory.create(
        term=red, language="it", name="Rosso", slug="rosso", status=PUBLISHED
    )
    blue = TermFactory.create(taxonomy=colour)
    TermTranslationFactory.create(term=blue, name="Blue", slug="blue", status=PUBLISHED)
    vendor = VendorFactory.create(
        status=Vendor.Status.ACTIVE, sells_online=True, slug="acme", display_name="Acme"
    )
    priced = listed("Steel bag", vendor=vendor, price_net=Decimal("12.50"))
    priced.terms.add(maker, red)
    on_request = listed("Custom bag", sales_mode=SalesMode.ON_REQUEST, price_net=None)
    on_request.terms.add(maker, blue)
    return {"maker": maker, "red": red, "blue": blue, "priced": priced, "on_request": on_request}


def soup(client: Client, path: str) -> Any:
    """The parsed page (bs4 ships with Wagtail; typed loosely to keep the assertions short)."""
    response = client.get(path)
    assert response.status_code == 200, response.status_code
    return BeautifulSoup(response.content, "html.parser")


def test_the_name_is_the_only_h1(client: Client, shop: dict[str, Any]) -> None:
    assert [
        h.get_text(strip=True) for h in soup(client, "/compatibility/maker/").find_all("h1")
    ] == ["Maker"]
    assert soup(client, "/it/compatibilita/marca/").h1.get_text(strip=True) == "Marca"


def test_the_head_has_title_canonical_robots_and_hreflang(
    client: Client, shop: dict[str, Any]
) -> None:
    page = soup(client, "/compatibility/maker/")
    assert "Maker" in page.title.get_text()
    assert (
        page.head.find("link", rel="canonical")["href"] == "http://testserver/compatibility/maker/"
    )
    assert page.head.find("meta", attrs={"name": "robots"})["content"] == "index, follow"
    hreflangs = {link["hreflang"] for link in page.head.find_all("link", hreflang=True)}
    assert {"en", "it", "x-default"} <= hreflangs


def test_no_inline_script_or_style(client: Client, shop: dict[str, Any]) -> None:
    page = soup(client, "/compatibility/maker/?colour=red")
    assert page.find("style") is None
    assert all(script.get("src") for script in page.find_all("script"))
    assert page.find(attrs={"style": True}) is None
    assert not [tag for tag in page.find_all(True) if any(a.startswith("on") for a in tag.attrs)]


def test_the_filter_form_is_a_plain_get_form_on_the_page_url(
    client: Client, shop: dict[str, Any]
) -> None:
    form = soup(client, "/it/compatibilita/marca/").find("form", attrs={"role": "search"})
    assert form["method"].lower() == "get"
    assert form["action"] == "/it/compatibilita/marca/"
    boxes = {(box["name"], box["value"]) for box in form.find_all("input", type="checkbox")}
    assert ("colore", "rosso") in boxes
    # A term without an IT text is offered with its EN slug (the explicit fallback).
    assert ("colore", "blue") in boxes
    assert form.find("input", attrs={"name": "price_min"})["type"] == "number"
    assert form.find("input", attrs={"name": "price_max"})["type"] == "number"
    vendors = [
        option["value"]
        for option in form.find("select", attrs={"name": "vendor"}).find_all("option")
    ]
    assert "acme" in vendors
    assert "" in vendors
    assert form.find("button", type="submit") is not None
    # Every control has a label.
    for control in form.find_all(["input", "select"]):
        assert form.find("label", attrs={"for": control["id"]}) is not None, control


def test_the_form_reflects_the_applied_filters(client: Client, shop: dict[str, Any]) -> None:
    form = soup(client, "/compatibility/maker/?colour=red&vendor=acme&price_min=10").find(
        "form", attrs={"role": "search"}
    )
    checked = {box["value"] for box in form.find_all("input", type="checkbox", checked=True)}
    assert checked == {"red"}
    assert form.find("input", attrs={"name": "price_min"})["value"] == "10"
    assert form.find("option", selected=True)["value"] == "acme"


def test_applied_filter_chips_link_to_the_canonical_url_without_them(
    client: Client, shop: dict[str, Any]
) -> None:
    page = soup(client, "/compatibility/maker/?colour=red&vendor=acme")
    chips = page.find(attrs={"aria-label": "Applied filters"})
    links = {a.get_text(" ", strip=True): a["href"] for a in chips.find_all("a")}
    assert links == {
        "Red (remove filter)": "/compatibility/maker/?vendor=acme",
        "Acme (remove filter)": "/compatibility/maker/?colour=red",
        "Clear all filters": "/compatibility/maker/",
    }


def test_product_cards_show_title_link_and_public_price(
    client: Client, shop: dict[str, Any]
) -> None:
    page = soup(client, "/compatibility/maker/")
    cards = page.find_all("article", class_="product-card")
    by_title = {card.find("h3").get_text(strip=True): card for card in cards}
    assert set(by_title) == {"Steel bag", "Custom bag"}
    priced = by_title["Steel bag"]
    assert priced.find("a")["href"] == "/product/steel-bag-en/"
    it_page = soup(client, "/it/compatibilita/marca/")
    assert it_page.find("a", string=re.compile("Steel bag IT"))["href"] == (
        "/it/prodotto/steel-bag-it/"
    )
    # The machine-readable price is never localised (the IT page shows "12,50").
    assert it_page.find("data")["value"] == "12.50"
    assert "12.50" in priced.get_text()
    assert "Price on request" in by_title["Custom bag"].get_text()


@pytest.mark.xfail(strict=True, reason="TAX.12: consumer prices must be VAT-inclusive")
def test_a_consumer_card_shows_a_vat_inclusive_price(client: Client, shop: dict[str, Any]) -> None:
    """Go-live marker (TAX.12, Directive 98/6/EC): CAT.8 cards show the net price "excl. VAT"
    as an interim. When TAX.12 makes consumer cards VAT-inclusive this test passes, strict xfail
    fails the suite, and the marker must be removed."""
    card = next(
        card
        for card in soup(client, "/compatibility/maker/").find_all("article", class_="product-card")
        if card.find("h3").get_text(strip=True) == "Steel bag"
    )
    text = card.get_text(" ", strip=True)
    assert "excl. VAT" not in text
    assert "VAT included" in text


def test_the_pagination_nav(
    client: Client, shop: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(services, "LISTING_PER_PAGE", 1)
    page = soup(client, "/compatibility/maker/?page=2")
    nav = page.find("nav", attrs={"aria-label": "Pagination"})
    current = nav.find(attrs={"aria-current": "page"})
    assert current.get_text(strip=True) == "2"
    assert nav.find("a", rel="prev")["href"] == "/compatibility/maker/"
    assert nav.find("a", rel="next") is None
    assert [a["href"] for a in nav.find_all("a") if a.get_text(strip=True) == "1"] == [
        "/compatibility/maker/"
    ]
    first = soup(client, "/compatibility/maker/?colour=blue,red")
    next_link = first.find("nav", attrs={"aria-label": "Pagination"}).find("a", rel="next")
    assert next_link["href"] == "/compatibility/maker/?colour=blue,red&page=2"


def test_the_empty_state(client: Client, shop: dict[str, Any]) -> None:
    page = soup(client, "/compatibility/maker/?price_min=1000")
    assert page.find("article", class_="product-card") is None
    assert "No products match" in page.get_text()
    assert page.find("nav", attrs={"aria-label": "Pagination"}) is None
    assert page.find("a", string=re.compile("Clear all filters"))["href"] == "/compatibility/maker/"


def form_query(form: Any, **extra: str) -> list[tuple[str, str]]:
    """What the browser sends for `form` as rendered, plus `extra` boxes ticked."""
    pairs = [
        (field["name"], field.get("value", ""))
        for field in form.find_all("input")
        if field.get("type") == "hidden"
        or (field.get("type") == "checkbox" and field.has_attr("checked"))
        or (field.get("type") == "number" and field.get("value"))
    ]
    pairs.extend(extra.items())
    return pairs


def test_submitting_the_form_keeps_an_applied_category(
    client: Client, shop: dict[str, Any]
) -> None:
    bags = CategoryFactory.create()
    CategoryTranslationFactory.create(category=bags, slug="bags", status=PUBLISHED)
    shop["priced"].category = bags
    shop["priced"].save()
    form = soup(client, "/compatibility/maker/?category=bags").find(
        "form", attrs={"role": "search"}
    )
    assert form.find("input", attrs={"type": "hidden", "name": "category"})["value"] == "bags"
    query = "&".join(f"{key}={value}" for key, value in form_query(form, colour="red"))
    response = client.get(f"{form['action']}?{query}", follow=True)
    assert response.status_code == 200
    assert response.context["params"].category == "bags"
    assert response.context["params"].term_slugs("colour") == ("red",)


def test_the_form_has_no_hidden_field_without_a_category(
    client: Client, shop: dict[str, Any]
) -> None:
    form = soup(client, "/compatibility/maker/?colour=red").find("form", attrs={"role": "search"})
    # The selected term is a ticked box, not a hidden field as well.
    assert form.find("input", type="hidden") is None


def test_a_card_on_a_fallback_language_page_links_to_the_source_language_url(
    client: Client, shop: dict[str, Any]
) -> None:
    product = ProductFactory.create(
        vendor=VendorFactory.create(status=Vendor.Status.ACTIVE, sells_online=True),
        status=Product.Status.PUBLISHED,
    )
    ProductTranslationFactory.create(
        product=product, title="Plain bag", slug="plain-bag-en", status=PUBLISHED
    )
    product.terms.add(shop["maker"])
    page = soup(client, "/it/compatibilita/marca/")
    assert page.find("a", string=re.compile("Plain bag"))["href"] == "/product/plain-bag-en/"


def test_the_cards_load_their_texts_in_a_fixed_number_of_queries(
    client: Client, shop: dict[str, Any]
) -> None:
    def count() -> int:
        with CaptureQueriesContext(connection) as queries:
            assert client.get("/compatibility/maker/").status_code == 200
        return len(queries)

    count()  # the first request also reads the operator setting once (then cached)
    before = count()
    for n in range(3):
        listed(f"Extra bag {n}").terms.add(shop["maker"])
    assert count() == before
