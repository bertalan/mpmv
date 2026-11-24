"""THM.25: the `ride` product card and listing chrome (ride fidelity 7/12).

A card is image first (the media before the title), sits on its own light surface (it sets its
own text, link and focus colours), has a vendor line and the price block with the owner's note,
and exactly one link target. The filter column is a card on a surface. The page banner takes an
optional image under a scrim. The listing loads its cards in a fixed number of queries.
"""

from decimal import Decimal
from typing import Any

import pytest
from bs4 import BeautifulSoup
from django.db import connection
from django.template.loader import render_to_string
from django.test import Client
from django.test.utils import CaptureQueriesContext
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.catalog.models import Product
from apps.catalog.services import ProductCard
from apps.catalog.tests.factories import (
    ProductFactory,
    ProductTranslationFactory,
    TaxonomyFactory,
    TaxonomyTranslationFactory,
    TermFactory,
    TermTranslationFactory,
)
from apps.core.models import TranslationStatus
from apps.core.tests.factories import PlatformOperatorFactory, utc
from apps.tax import engine as tax
from apps.tax.tests.factories import TaxRateFactory, VendorTaxProfileFactory
from apps.themes.services import use_theme
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory
from tests.i18n import translated

pytestmark = pytest.mark.django_db

PUBLISHED = TranslationStatus.PUBLISHED


@pytest.fixture
def taxed() -> None:
    PlatformOperatorFactory.create(open_ended=True, valid_from=utc(2026, 1, 1), country="IT")
    TaxRateFactory.create(
        country="IT", category=tax.STANDARD, rate=Decimal("22"), valid_from=utc(2026, 1, 1)
    )


def _vendor(**fields: Any) -> Vendor:
    vendor = VendorFactory.create(
        status=Vendor.Status.ACTIVE, sells_online=True, country="IT", **fields
    )
    VendorTaxProfileFactory.create(vendor=vendor, valid_from=utc(2026, 1, 1))
    return vendor


def _card(**overrides: Any) -> ProductCard:
    values: dict[str, Any] = {
        "title": "Oak lamp",
        "url": "/p/oak-lamp/",
        "price": Decimal("10.00"),
        "vendor": _vendor(),
    }
    values.update(overrides)
    return ProductCard(**values)


def _render(card: ProductCard) -> Any:
    with use_theme("ride"):
        html = render_to_string(
            "catalog/includes/product_card.html", {"card": card, "currency": "EUR"}
        )
    return BeautifulSoup(html, "html.parser").select_one("article.product-card")


def test_a_card_is_image_first_on_its_own_light_surface(taxed: None) -> None:
    article = _render(_card())
    assert article is not None
    assert "surface-light" in article["class"]
    media = article.select_one(".product-card__media")
    assert media is not None
    assert media.find("img") is not None
    assert media.find_next("h3") is not None  # the media comes before the title in the source
    assert article.select_one("h3.product-card-title") is not None


def test_a_card_has_one_link_target(taxed: None) -> None:
    article = _render(_card())
    assert article is not None
    assert len(article.find_all("a")) == 1
    assert article.find("a")["href"] == "/p/oak-lamp/"
    assert article.select_one("img")["alt"] == ""  # the image is decorative next to the title


def test_a_card_without_a_url_has_no_link(taxed: None) -> None:
    article = _render(_card(url=None))
    assert article is not None
    assert article.find("a") is None


def test_the_vendor_line_names_the_seller(taxed: None) -> None:
    vendor = _vendor(display_name="Workshop Nine")
    line = _render(_card(vendor=vendor)).select_one(".product-card-vendor")
    assert line is not None
    assert "Workshop Nine" in line.get_text()


def test_the_price_block_keeps_the_amount_hook_and_the_small_note(taxed: None) -> None:
    price = _render(_card()).select_one(".product-card-price")
    assert price is not None
    assert price.find("data")["value"] == "12.20"
    note = price.select_one("small.price-note")
    assert note is not None
    assert note.get_text(strip=True)


def test_a_card_with_no_price_says_so(taxed: None) -> None:
    price = _render(_card(price=None)).select_one(".product-card-price")
    assert price is not None
    assert translated("Price on request") in price.get_text()


def test_the_page_banner_takes_an_optional_image_under_a_scrim() -> None:
    with use_theme("ride"):
        html = render_to_string(
            "components/page_banner.html",
            {"title": "Bags", "items": [], "image_url": "/media/banner.webp"},
        )
    soup = BeautifulSoup(html, "html.parser")
    image = soup.select_one("img.page-banner__image")
    assert image is not None
    assert image["alt"] == ""
    assert soup.select_one(".page-banner__scrim") is not None
    assert len(soup.find_all("h1")) == 1


@pytest.fixture
def shop(settings: SettingsWrapper) -> dict[str, Any]:
    settings.MPMV_THEME_DEFAULT = "ride"
    taxonomy = TaxonomyFactory.create(code="compat", has_landing=True, filterable=False)
    TaxonomyTranslationFactory.create(
        taxonomy=taxonomy, name="Compatibility", slug="compatibility", status=PUBLISHED
    )
    maker = TermFactory.create(taxonomy=taxonomy)
    TermTranslationFactory.create(term=maker, name="Maker", slug="maker", status=PUBLISHED)
    return {"maker": maker}


def _listed(shop: dict[str, Any], name: str) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE, sells_online=True)
    product = ProductFactory.create(
        vendor=vendor, status=Product.Status.PUBLISHED, price_net=Decimal("10.00")
    )
    ProductTranslationFactory.create(
        product=product, title=name, slug=name.lower().replace(" ", "-"), status=PUBLISHED
    )
    product.terms.add(shop["maker"])


def test_the_filter_column_is_a_card_on_a_light_surface(client: Client, shop: dict) -> None:  # type: ignore[type-arg]
    _listed(shop, "Bag one")
    response = client.get("/compatibility/maker/", follow=True)
    soup = BeautifulSoup(response.content, "html.parser")
    filters = soup.select_one("section.listing-filters")
    assert filters is not None
    assert {"card", "surface-light"} <= set(filters["class"])
    assert filters.select_one("form") is not None


def test_the_listing_cards_each_have_a_vendor_line_and_one_link(
    client: Client,
    shop: dict,  # type: ignore[type-arg]
) -> None:
    _listed(shop, "Bag one")
    _listed(shop, "Bag two")
    soup = BeautifulSoup(client.get("/compatibility/maker/", follow=True).content, "html.parser")
    cards = soup.select("ul.product-grid article.product-card")
    assert len(cards) == 2
    for card in cards:
        assert len(card.find_all("a")) == 1
        assert card.select_one(".product-card-vendor") is not None


def test_the_listing_loads_its_cards_in_a_fixed_number_of_queries(
    client: Client,
    shop: dict,  # type: ignore[type-arg]
) -> None:
    def count() -> int:
        with CaptureQueriesContext(connection) as queries:
            assert client.get("/compatibility/maker/", follow=True).status_code == 200
        return len(queries)

    _listed(shop, "Bag one")
    _listed(shop, "Bag two")
    count()  # the first request also warms the operator setting cache
    few = count()
    for n in range(6):
        _listed(shop, f"Extra bag {n}")
    assert count() == few
