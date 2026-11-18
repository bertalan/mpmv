"""CAT.15 (owner decision Q-33): "Other sellers of this product".

Done when: two vendors' products with the same GTIN list each other; the same MPN with a
different brand does not match; the same brand and MPN does. The block is bounded, costs the
same queries whatever the number of sellers, and shows published products of active vendors in
the page language only, with the public seller name and the consumer price of the cards.
"""

from decimal import Decimal
from typing import Any

import pytest
from django.apps import apps
from django.db import connection
from django.template.loader import render_to_string
from django.test import Client
from django.test.utils import CaptureQueriesContext
from wagtail.models import Site

from apps.catalog import product_pages
from apps.catalog.models import Product
from apps.catalog.tests.factories import BrandFactory, ProductFactory, ProductTranslationFactory
from apps.core.i18n import configured_languages, primary_language
from apps.core.models import TranslationStatus
from apps.themes.services import use_theme
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory
from tests.i18n import other_language, product_url

pytestmark = pytest.mark.django_db

PUBLISHED = TranslationStatus.PUBLISHED
GTIN = "4006381333931"


@pytest.fixture(autouse=True)
def _site_allows_indexing() -> None:
    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )


def offer(
    *, title: str, languages: tuple[str, ...] | None = None, vendor: Vendor | None = None, **kw: Any
) -> Product:
    """A published product with a published text per language."""
    kw.setdefault("status", Product.Status.PUBLISHED)
    product = ProductFactory.create(
        vendor=vendor or VendorFactory.create(status=Vendor.Status.ACTIVE, sells_online=True), **kw
    )
    for language in languages or tuple(configured_languages()):
        ProductTranslationFactory.create(
            product=product,
            language=language,
            title=f"{title} {language}",
            slug=f"{title.lower().replace(' ', '-')}-{language}",
            status=PUBLISHED,
        )
    return product


def titles(product: Product, language: str | None = None) -> list[str]:
    cards = product_pages.other_sellers(product, language or primary_language())
    return [card.title for card in cards]


def test_the_same_gtin_lists_each_other() -> None:
    first = offer(title="Mug A", gtin=GTIN)
    second = offer(title="Mug B", gtin=GTIN)
    assert titles(first) == [f"Mug B {primary_language()}"]
    assert titles(second) == [f"Mug A {primary_language()}"]


def test_a_different_or_empty_gtin_does_not_match() -> None:
    first = offer(title="Mug A", gtin=GTIN)
    offer(title="Mug B", gtin="4006381333948")
    offer(title="Mug C", gtin="")
    assert titles(first) == []
    nothing = offer(title="Mug D", gtin="")
    offer(title="Mug E", gtin="")
    assert titles(nothing) == []  # two empty identifiers are not a match


def test_the_same_brand_and_mpn_match() -> None:
    brand = BrandFactory.create()
    first = offer(title="Mug A", brand=brand, mpn="MG-1")
    offer(title="Mug B", brand=brand, mpn="MG-1")
    assert titles(first) == [f"Mug B {primary_language()}"]


def test_the_same_mpn_with_another_brand_or_no_brand_does_not_match() -> None:
    first = offer(title="Mug A", brand=BrandFactory.create(), mpn="MG-1")
    offer(title="Mug B", brand=BrandFactory.create(), mpn="MG-1")
    offer(title="Mug C", brand=None, mpn="MG-1")
    assert titles(first) == []


def test_a_brand_without_an_mpn_does_not_match() -> None:
    brand = BrandFactory.create()
    first = offer(title="Mug A", brand=brand, mpn="")
    offer(title="Mug B", brand=brand, mpn="")
    assert titles(first) == []


def test_a_brand_spelled_differently_is_the_same_brand_entity() -> None:
    """Brands are one shared entity (CAT.20): case and accent variants are one row."""
    brand = BrandFactory.create(name="Acme")
    first = offer(title="Mug A", brand=brand, mpn="MG-1")
    offer(title="Mug B", brand=brand, mpn="MG-1")
    assert len(titles(first)) == 1


def test_exclusions() -> None:
    mine = VendorFactory.create(status=Vendor.Status.ACTIVE, sells_online=True)
    first = offer(title="Mug A", gtin=GTIN, vendor=mine)
    offer(title="Mug Same Vendor", gtin=GTIN, vendor=mine)
    offer(title="Mug Draft", gtin=GTIN, status=Product.Status.DRAFT)
    offer(title="Mug Retired", gtin=GTIN, status=Product.Status.RETIRED)
    for status in (Vendor.Status.SUSPENDED, Vendor.Status.CLOSED, Vendor.Status.APPLICANT):
        offer(title=f"Mug {status}", gtin=GTIN, vendor=VendorFactory.create(status=status))
    offer(title="Mug Live", gtin=GTIN)
    assert titles(first) == [f"Mug Live {primary_language()}"]


def test_a_product_not_published_in_the_page_language_is_left_out() -> None:
    second = other_language()
    first = offer(title="Mug A", gtin=GTIN)
    offer(title="Mug Only Primary", gtin=GTIN, languages=(primary_language(),))
    offer(title="Mug Both", gtin=GTIN)
    hidden = offer(title="Mug Machine", gtin=GTIN)
    hidden.translations.filter(language=second).update(status=TranslationStatus.MACHINE)
    assert sorted(titles(first, second)) == [f"Mug Both {second}"]
    assert len(titles(first, primary_language())) == 3


def test_ordered_by_price_then_name_and_bounded() -> None:
    first = offer(title="Mug A", gtin=GTIN)
    offer(title="Mug Z", gtin=GTIN, price_net=Decimal("5.00"))
    offer(title="Mug B", gtin=GTIN, price_net=Decimal("9.00"))
    offer(title="Mug C", gtin=GTIN, price_net=Decimal("9.00"))
    offer(title="Mug Q", gtin=GTIN, price_net=Decimal("20.00"))
    language = primary_language()
    assert titles(first) == [
        f"Mug Z {language}",
        f"Mug B {language}",
        f"Mug C {language}",
        f"Mug Q {language}",
    ]
    cards = product_pages.other_sellers(first, language, limit=2)
    assert [card.title for card in cards] == [f"Mug Z {language}", f"Mug B {language}"]


def test_the_number_of_queries_does_not_grow_with_the_sellers() -> None:
    first = offer(title="Mug A", gtin=GTIN)
    offer(title="Mug B", gtin=GTIN)

    def count() -> int:
        with CaptureQueriesContext(connection) as queries:
            product_pages.other_sellers(first, primary_language())
        return len(queries)

    few = count()
    for index in range(5):
        offer(title=f"Mug N{index}", gtin=GTIN)
    assert count() == few
    assert few <= 3


@pytest.mark.usefixtures("consumer_prices")
def test_the_page_lists_the_other_sellers_with_consumer_prices(client: Client) -> None:
    language = primary_language()
    offer(title="Mug A", gtin=GTIN)
    other = offer(
        title="Mug B",
        gtin=GTIN,
        price_net=Decimal("10.00"),
        vendor=VendorFactory.create(
            status=Vendor.Status.ACTIVE,
            sells_online=True,
            display_name="Shop Beta",
            legal_form=Vendor.LegalForm.SRL,
        ),
    )
    html = client.get(product_url(language, f"mug-a-{language}")).content.decode()
    assert 'class="product-other-sellers' in html
    assert product_url(language, f"mug-b-{language}") in html
    assert "Shop Beta" in html
    assert "12.20" in html  # 10.00 + 22 % (TAX.12), never the net price
    assert str(other.sku) not in html


def test_the_page_has_no_block_without_other_sellers(client: Client) -> None:
    language = primary_language()
    offer(title="Mug A", gtin=GTIN)
    html = client.get(product_url(language, f"mug-a-{language}")).content.decode()
    assert "product-other-sellers" not in html


def test_a_private_person_is_not_named_on_the_page(client: Client) -> None:
    language = primary_language()
    offer(title="Mug A", gtin=GTIN)
    person = VendorFactory.create(
        status=Vendor.Status.ACTIVE,
        sells_online=True,
        display_name="Mario Rossi",
        legal_name="Mario Rossi",
        first_name="Mario",
        last_name="Rossi",
        legal_form=Vendor.LegalForm.SOLE_PROPRIETORSHIP,
    )
    offer(title="Mug B", gtin=GTIN, vendor=person)
    html = client.get(product_url(language, f"mug-a-{language}")).content.decode()
    assert "product-other-sellers" in html
    assert "Mario Rossi" not in html


@pytest.mark.usefixtures("consumer_prices")
def test_the_ride_block_renders() -> None:
    first = offer(title="Mug A", gtin=GTIN)
    offer(title="Mug B", gtin=GTIN)
    cards = product_pages.other_sellers(first, primary_language())
    with use_theme("ride"):
        html = render_to_string(
            "catalog/includes/other_sellers.html", {"other_sellers": cards, "currency": "EUR"}
        )
    assert "product-other-sellers" in html
    assert "product-card" in html
    assert "Mug B" in html
