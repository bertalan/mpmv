"""TAX.12: consumer prices include VAT (Directive 98/6/EC; owner decision MQ-12, 2026-11-08,
note wording 2026-11-16). Go-live gate.

Done when: IT, DE and FR consumers get the destination rate and the owner's note; a business
buyer's net view after the opt-in; `on_request` products never priced; ROUND_HALF_UP per line;
the catalog has no tax import; both themes render the gross price and the note, variant prices
included; the note in the five languages.

These tests join catalog, cms, orders and tax, so they live outside the apps (the import
contracts keep catalog and tax apart, tests included).
"""

from decimal import Decimal
from typing import Any

import pytest
from bs4 import BeautifulSoup
from django.apps import apps
from django.conf import settings
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.test import Client, RequestFactory
from django.utils import translation
from pytest_django.fixtures import Settings as SettingsWrapper
from wagtail.models import Site

from apps.catalog import slugs
from apps.catalog.models import Product
from apps.catalog.services import ProductCard
from apps.catalog.tests.factories import (
    ProductFactory,
    ProductTranslationFactory,
    VariantFactory,
    VariantTranslationFactory,
)
from apps.core.i18n import configured_languages
from apps.core.models import TranslationStatus
from apps.core.tests.factories import PlatformOperatorFactory, utc
from apps.orders.ui import present
from apps.tax import prices
from apps.tax.tests.factories import TaxRateFactory
from apps.themes.services import use_theme
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

NOTES = {
    "en": "Indicative price. Final amount at checkout.",
    "it": "Prezzo indicativo. Importo finale al checkout.",
    "de": "Unverbindlicher Preis. Endbetrag an der Kasse.",
    "fr": "Prix indicatif. Montant final lors du paiement.",
    "es": "Precio orientativo. Importe final al finalizar la compra.",
}
NET = Decimal("12.50")


@pytest.fixture(autouse=True)
def _shop(db: None) -> None:
    """An Italian operator, the IT, DE and FR standard rates, and an indexable site."""
    PlatformOperatorFactory.create(open_ended=True, valid_from=utc(2020), country="IT")
    for country, rate in (("IT", "22"), ("DE", "19"), ("FR", "20")):
        TaxRateFactory.create(
            open_ended=True, valid_from=utc(2020), country=country, rate=Decimal(rate)
        )
    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )


def live(**kwargs: Any) -> Product:
    kwargs.setdefault(
        "vendor", VendorFactory.create(status=Vendor.Status.ACTIVE, sells_online=True)
    )
    kwargs.setdefault("status", Product.Status.PUBLISHED)
    kwargs.setdefault("price_net", NET)
    product = ProductFactory.create(**kwargs)
    for language in configured_languages():
        ProductTranslationFactory.create(
            product=product,
            language=language,
            title=f"Mug {language}",
            slug=f"mug-{language}",
            status=TranslationStatus.PUBLISHED,
        )
    return product


def page(client: Client, language: str) -> Any:
    url = slugs.product_path(language, f"mug-{language}", default_language=settings.LANGUAGE_CODE)
    response = client.get(url)
    assert response.status_code == 200, url
    return BeautifulSoup(response.content, "html.parser")


def request(language: str = "en", session: dict[str, Any] | None = None) -> HttpRequest:
    req = RequestFactory().get("/")
    req.session = session if session is not None else {}  # type: ignore[assignment]
    req.LANGUAGE_CODE = language
    return req


# --- The public API (SEO.1 calls it for JSON-LD Offer.price)


@pytest.mark.parametrize(
    ("country", "rate", "gross"),
    [
        ("IT", Decimal("22.0000"), Decimal("15.25")),
        ("DE", Decimal("19.0000"), Decimal("14.88")),  # 2.375 VAT, half up
        ("FR", Decimal("20.0000"), Decimal("15.00")),
    ],
)
def test_a_consumer_gets_the_destination_rate(country: str, rate: Decimal, gross: Decimal) -> None:
    price = prices.consumer_price(NET, "standard", country=country)
    assert (price.gross, price.rate, price.country, price.estimated) == (
        gross,
        rate,
        country,
        False,
    )
    assert price.amount == gross
    assert price.vat_included
    assert price.note == NOTES["en"]


@pytest.mark.parametrize(
    ("language", "country", "gross"),
    [
        ("it", "IT", Decimal("15.25")),
        ("de", "DE", Decimal("14.88")),
        ("fr", "FR", Decimal("15.00")),
    ],
)
def test_before_an_address_the_language_gives_the_estimated_country(
    language: str, country: str, gross: Decimal
) -> None:
    with translation.override(language):
        price = prices.consumer_price(NET, "standard", request=request(language))
    assert (price.country, price.estimated, price.gross) == (country, True, gross)


def test_english_falls_back_to_the_operator_country() -> None:
    with translation.override("en"):
        price = prices.consumer_price(NET, "standard", request=request())
    assert (price.country, price.estimated, price.gross) == ("IT", True, Decimal("15.25"))


def test_the_delivery_country_given_at_checkout_wins_over_the_language() -> None:
    req = request("it")
    prices.remember_delivery_country(req, "DE")
    with translation.override("it"):
        price = prices.consumer_price(NET, "standard", request=req)
    assert (price.country, price.estimated, price.gross) == ("DE", False, Decimal("14.88"))


def test_a_business_buyer_sees_the_net_price_only_after_the_opt_in() -> None:
    req = request()
    assert prices.consumer_price(NET, "standard", request=req).amount == Decimal("15.25")
    prices.set_business_net_view(req, True)
    price = prices.consumer_price(NET, "standard", request=req)
    assert (price.amount, price.gross, price.vat_included) == (NET, Decimal("15.25"), False)
    assert price.note == "excl. VAT"
    prices.set_business_net_view(req, False)
    assert prices.consumer_price(NET, "standard", request=req).business_net_view is False


def test_a_missing_rate_or_operator_gives_no_price() -> None:
    with pytest.raises(prices.PriceUnavailable):
        prices.consumer_price(NET, "standard", country="ES")
    with pytest.raises(prices.PriceUnavailable):
        prices.consumer_price(NET, "book_art74", country="IT")
    apps.get_model("core", "PlatformOperator").objects.all().delete()
    with pytest.raises(prices.PriceUnavailable):
        prices.consumer_price(NET, "standard", country="IT")


def test_the_rate_tables_are_read_once_per_request(
    django_assert_max_num_queries: Any,
) -> None:
    req = request()
    prices.consumer_price(NET, "standard", request=req)
    with django_assert_max_num_queries(0):
        for _ in range(5):
            prices.consumer_price(NET, "standard", request=req)


# --- The note in the five languages


@pytest.mark.parametrize("language", sorted(NOTES))
def test_the_owner_note_in_each_language(language: str) -> None:
    with translation.override(language):
        assert str(prices.PRICE_NOTE) == NOTES[language]


# --- Product page, variants and on request (base theme, then ride)


@pytest.mark.parametrize(
    ("language", "amount"), [("it", "15,25"), ("de", "14,88"), ("fr", "15,00")]
)
def test_the_product_page_shows_the_destination_gross_price_and_the_note(
    client: Client, language: str, amount: str
) -> None:
    live()
    price = page(client, language).find(class_="product-price")
    assert price.find("data")["value"] == amount.replace(",", ".")
    text = price.get_text(" ", strip=True)
    assert amount in text
    assert NOTES[language] in text
    assert "12,50" not in text
    assert "12.50" not in text


def test_variant_prices_are_gross(client: Client) -> None:
    product = live(kind=Product.Kind.VARIABLE, price_net=Decimal("10.00"))
    variant = VariantFactory.create(
        product=product, sku="VAR-X", is_default=True, price_net_override=Decimal("24.00")
    )
    VariantTranslationFactory.create(variant=variant, language="en", label="Large")
    shown = page(client, "en").find(class_="variant-price")
    assert shown.find("data")["value"] == "29.28"  # 24.00 + 22 %
    assert NOTES["en"] in shown.get_text(" ", strip=True)


def test_an_on_request_product_is_never_priced(client: Client) -> None:
    live(sales_mode=Product.SalesMode.ON_REQUEST, price_net=Decimal("120.00"))
    buy = page(client, "en").find(class_="product-buy")
    text = buy.get_text(" ", strip=True)
    assert "Price on request" in text
    assert buy.find("data") is None
    assert "146.40" not in text
    assert "120.00" not in text
    assert NOTES["en"] not in text


def test_the_ride_theme_renders_the_gross_price_and_the_note(
    client: Client, settings: SettingsWrapper
) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    live()
    price = page(client, "it").find(class_="product-price")
    assert price.find("data")["value"] == "15.25"
    assert NOTES["it"] in price.get_text(" ", strip=True)


def test_a_business_buyer_who_opted_in_sees_net_prices_on_the_page(client: Client) -> None:
    live()
    session = client.session
    session[prices.BUSINESS_NET_VIEW_KEY] = True
    session.save()
    price = page(client, "en").find(class_="product-price")
    assert price.find("data")["value"] == "12.50"
    assert "excl. VAT" in price.get_text(" ", strip=True)


# --- Cards and tiles, in both themes


CARD = ProductCard(title="Mug", url="/product/mug/", price=NET, fiscal_category="standard")


@pytest.mark.parametrize("theme", ["base", "ride"])
def test_a_catalog_card_shows_the_gross_price_and_the_note(theme: str) -> None:
    with use_theme(theme), translation.override("de"):
        html = render_to_string(
            "catalog/includes/product_card.html",
            {"card": CARD, "currency": "EUR", "request": request("de")},
        )
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    assert "EUR 14,88" in text
    assert NOTES["de"] in text
    assert "12,50" not in text


@pytest.mark.parametrize("theme", ["base", "ride"])
def test_a_cms_tile_shows_the_gross_price_and_the_note(theme: str) -> None:
    on_request = ProductCard(title="Custom", url=None, price=None)
    with use_theme(theme), translation.override("fr"):
        html = render_to_string(
            "cms/includes/product_tiles.html",
            {
                "cards": [CARD, on_request],
                "currency": "EUR",
                "heading_level": 3,
                "request": request("fr"),
            },
        )
    tiles: Any = BeautifulSoup(html, "html.parser").find_all("article")
    priced = tiles[0].get_text(" ", strip=True)
    assert "EUR 15,00" in priced
    assert NOTES["fr"] in priced
    assert tiles[0].find(class_="product-tile__note") is not None
    unpriced = tiles[1].get_text(" ", strip=True)
    assert "Prix sur demande" in unpriced or "Price on request" in unpriced
    assert NOTES["fr"] not in unpriced


def test_a_card_without_a_rate_shows_no_net_price() -> None:
    card = ProductCard(title="Book", url=None, price=NET, fiscal_category="book_art74")
    html = render_to_string(
        "catalog/includes/product_card.html",
        {"card": card, "currency": "EUR", "request": request()},
    )
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    assert "12.50" not in text
    assert "Price at checkout" in text


# --- Cart lines (ROUND_HALF_UP per line)


def test_cart_lines_before_the_address_use_the_estimated_rate() -> None:
    with translation.override("de"):
        unit, total = present.line_prices(Decimal("0.25"), 3, "standard", request=request("de"))
    # 0.25 + 19 % = 0.2975 -> 0.30; the line 0.75 + 0.1425 -> 0.89 (not 3 x 0.30).
    assert (unit, total) == (Decimal("0.30"), Decimal("0.89"))


def test_cart_lines_after_the_address_use_the_engine_decision() -> None:
    unit, total = present.line_prices(
        Decimal("0.25"),
        3,
        "standard",
        request=request(),
        line_net=Decimal("0.75"),
        tax_rate=Decimal("22.0000"),
        line_tax=Decimal("0.17"),
    )
    assert (unit, total) == (Decimal("0.31"), Decimal("0.92"))


def test_cart_lines_for_a_business_buyer_who_opted_in_are_net() -> None:
    req = request()
    prices.set_business_net_view(req, True)
    assert present.line_prices(Decimal("0.25"), 3, "standard", request=req) == (
        Decimal("0.25"),
        Decimal("0.75"),
    )
