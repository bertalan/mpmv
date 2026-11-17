"""TAX.12: consumer prices include VAT (Directive 98/6/EC; owner decision MQ-12, 2026-11-08,
note wording 2026-11-16). Go-live gate.

A displayed price is what checkout charges for the vendor and the destination (owner decision
"il prezzo reale", 2026-11-16): an exempt vendor shows no VAT, a domestic-only vendor its own
country's rate. Done when: IT, DE and FR consumers get the destination rate and the owner's
note; a business buyer's net view after the opt-in; `on_request` products never priced;
ROUND_HALF_UP per line; the catalog has no tax import; both themes render the gross price and
the note, variant prices included; the note in the five languages.

These tests join catalog, cms, orders and tax, so they live outside the apps (the import
contracts keep catalog and tax apart, tests included).
"""

import datetime as dt
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
from apps.core.i18n import configured_languages, primary_language
from apps.core.models import TranslationStatus
from apps.core.tests.factories import utc
from apps.orders.ui import present
from apps.tax import prices
from apps.tax.models import VendorTaxProfile
from apps.tax.tests.factories import (
    TaxRateFactory,
    VendorTaxProfileFactory,
    VendorTaxProfileTranslationFactory,
)
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
def _shop(consumer_prices: None) -> None:
    """The `consumer_prices` operator, rates and OSS vendors; the books, zero and UK rates the
    edge cases need; an indexable site."""
    for country, category, rate in (("IT", "books", "4"), ("IT", "zero", "0")):
        TaxRateFactory.create(
            open_ended=True,
            valid_from=utc(2020),
            country=country,
            category=category,
            rate=Decimal(rate),
        )
    TaxRateFactory.create(open_ended=True, valid_from=utc(2020), country="GB", rate=Decimal("20"))
    # Livigno (IT 23041) is in the seeded territory table (tax migration 0006).
    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )


def seller(regime: str = "oss", country: str = "IT", **profile: Any) -> Vendor:
    """An active vendor selling online whose tax profile is `regime` (replacing the OSS one)."""
    found = VendorFactory.create(status=Vendor.Status.ACTIVE, sells_online=True, country=country)
    VendorTaxProfile.objects.filter(vendor=found).delete()
    VendorTaxProfileFactory.create(
        vendor=found,
        regime=regime,
        valid_from=utc(2020),
        oss_registered_since=dt.date(2020, 1, 1) if regime == "oss" else None,
        **profile,
    )
    return found


def live(**kwargs: Any) -> Product:
    kwargs.setdefault("vendor", seller())
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


@pytest.fixture(autouse=True)
def _vendor(_shop: None) -> None:
    global VENDOR
    VENDOR = seller()


VENDOR: Vendor


@pytest.mark.parametrize(
    ("country", "rate", "gross"),
    [
        ("IT", Decimal("22.0000"), Decimal("15.25")),
        ("DE", Decimal("19.0000"), Decimal("14.88")),  # 2.375 VAT, half up
        ("FR", Decimal("20.0000"), Decimal("15.00")),
    ],
)
def test_a_consumer_gets_the_destination_rate(country: str, rate: Decimal, gross: Decimal) -> None:
    price = prices.consumer_price(NET, "standard", vendor=VENDOR, country=country)
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
        price = prices.consumer_price(NET, "standard", vendor=VENDOR, request=request(language))
    assert (price.country, price.estimated, price.gross) == (country, True, gross)


def test_english_falls_back_to_the_operator_country() -> None:
    with translation.override("en"):
        price = prices.consumer_price(NET, "standard", vendor=VENDOR, request=request())
    assert (price.country, price.estimated, price.gross) == ("IT", True, Decimal("15.25"))


def test_the_delivery_country_given_at_checkout_wins_over_the_language() -> None:
    req = request("it")
    prices.remember_delivery_address(req, "DE")
    with translation.override("it"):
        price = prices.consumer_price(NET, "standard", vendor=VENDOR, request=req)
    assert (price.country, price.estimated, price.gross) == ("DE", False, Decimal("14.88"))


def test_a_business_buyer_sees_the_net_price_only_after_the_opt_in() -> None:
    req = request()
    first = prices.consumer_price(NET, "standard", vendor=VENDOR, request=req)
    assert first.amount == Decimal("15.25")
    prices.set_business_net_view(req, True)
    price = prices.consumer_price(NET, "standard", vendor=VENDOR, request=req)
    assert (price.amount, price.gross, price.vat_included) == (NET, Decimal("15.25"), False)
    assert price.note == "excl. VAT"
    prices.set_business_net_view(req, False)
    again = prices.consumer_price(NET, "standard", vendor=VENDOR, request=req)
    assert again.business_net_view is False


def test_a_missing_rate_profile_or_operator_gives_no_price() -> None:
    with pytest.raises(prices.PriceUnavailable):
        prices.consumer_price(NET, "standard", vendor=VENDOR, country="ES")
    with pytest.raises(prices.PriceUnavailable):
        prices.consumer_price(NET, "luxury", vendor=VENDOR, country="IT")
    VendorTaxProfile.objects.filter(vendor=VENDOR).delete()
    with pytest.raises(prices.PriceUnavailable):
        prices.consumer_price(NET, "standard", vendor=VENDOR, country="IT")
    apps.get_model("core", "PlatformOperator").objects.all().delete()
    with pytest.raises(prices.PriceUnavailable):
        prices.consumer_price(NET, "standard", vendor=VENDOR, country="IT")


def test_the_rate_tables_and_profiles_are_read_once_per_request(
    django_assert_max_num_queries: Any,
) -> None:
    others = [seller() for _ in range(3)]
    req = request()
    prices.consumer_price(NET, "standard", vendor=VENDOR, request=req)
    with django_assert_max_num_queries(0):
        for vendor in [VENDOR, *others] * 2:
            prices.consumer_price(NET, "standard", vendor=vendor, request=req)


# --- The real price: the vendor's regime decides (owner decision 2026-11-16)


def test_a_forfettario_vendor_shows_the_net_price_as_the_final_price(client: Client) -> None:
    vendor = seller("exempt", exempt_reason="flat_rate_forfettario")
    for country in ("IT", "DE", "FR"):
        price = prices.consumer_price(NET, "standard", vendor=vendor, country=country)
        assert (price.gross, price.rate) == (NET, Decimal(0))
    live(vendor=vendor)
    shown = page(client, "it").find(class_="product-price")
    assert shown.find("data")["value"] == "12.50"
    assert NOTES["it"] in shown.get_text(" ", strip=True)


def test_an_rf20_vendor_shows_what_checkout_charges(client: Client) -> None:
    """RF20 (cross-border franchise) has no regime of its own in the engine yet (TAX.20): the
    vendor's profile decides, here an exemption with its legal note, so no VAT is added."""
    vendor = seller("exempt", country="DE", exempt_reason="other", exempt_country="DE")
    vendor.fiscal_regime = "RF20"
    vendor.save(update_fields=["fiscal_regime"])
    profile = VendorTaxProfile.objects.get(vendor=vendor)
    VendorTaxProfileTranslationFactory.create(profile=profile, language=primary_language())
    price = prices.consumer_price(NET, "standard", vendor=vendor, country="IT")
    assert (price.gross, price.rate) == (NET, Decimal(0))
    live(vendor=vendor)
    assert page(client, "en").find(class_="product-price").find("data")["value"] == "12.50"


def test_a_domestic_only_vendor_seen_from_germany_shows_its_own_rate() -> None:
    vendor = seller("domestic_only")
    with translation.override("de"):
        price = prices.consumer_price(NET, "standard", vendor=vendor, request=request("de"))
    # Italian VAT (22 %), not the German 19 % the vendor will not charge.
    assert (price.country, price.rate, price.gross) == ("DE", Decimal("22.0000"), Decimal("15.25"))


def test_an_art74_publisher_book_shows_no_vat_and_a_bookshop_the_books_rate() -> None:
    publisher = seller("exempt", exempt_reason="art74_publishing")
    book = prices.consumer_price(NET, "book_art74", vendor=publisher, country="IT")
    assert book.gross == NET
    shop = prices.consumer_price(NET, "book_art74", vendor=VENDOR, country="IT")
    assert (shop.rate, shop.gross) == (Decimal("4.0000"), Decimal("13.00"))


# --- Destinations after an address, territories and Northern Ireland


def test_a_remembered_postcode_in_a_territory_outside_the_vat_area_shows_no_vat() -> None:
    req = request("it")
    prices.remember_delivery_address(req, "IT", "23041")  # Livigno
    with translation.override("it"):
        price = prices.consumer_price(NET, "standard", vendor=VENDOR, request=req)
    assert (price.country, price.estimated, price.gross) == ("IT", False, NET)


def test_a_remembered_northern_ireland_address_follows_the_xi_rules() -> None:
    req = request()
    prices.remember_delivery_address(req, "GB", "BT1 1AA")
    price = prices.consumer_price(NET, "standard", vendor=VENDOR, request=req)
    assert (price.country, price.rate, price.gross) == ("XI", Decimal("20.0000"), Decimal("15.00"))


def test_a_language_estimate_never_uses_a_remembered_postcode_of_another_country() -> None:
    req = request("it", {prices.DELIVERY_POSTCODE_KEY: "23041"})
    with translation.override("it"):
        price = prices.consumer_price(NET, "standard", vendor=VENDOR, request=req)
    assert (price.estimated, price.gross) == (True, Decimal("15.25"))


def test_a_zero_rated_category_adds_nothing() -> None:
    price = prices.consumer_price(NET, "zero", vendor=VENDOR, country="IT")
    assert (price.rate, price.gross) == (Decimal("0.0000"), NET)


def test_half_a_cent_of_vat_is_rounded_up() -> None:
    # 0.05 at 10 % is 0.005 of VAT: 0.01, half up (a reduced IT rate for this case).
    TaxRateFactory.create(
        open_ended=True, valid_from=utc(2020), country="IT", category="reduced", rate=Decimal(10)
    )
    price = prices.consumer_price(Decimal("0.05"), "reduced", vendor=VENDOR, country="IT")
    assert price.gross == Decimal("0.06")


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
    VariantTranslationFactory.create(variant=variant, language=primary_language(), label="Large")
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


def card(**kwargs: Any) -> ProductCard:
    kwargs.setdefault("vendor", VENDOR)
    return ProductCard(title="Mug", url="/product/mug/", price=NET, **kwargs)


@pytest.mark.parametrize("theme", ["base", "ride"])
def test_a_catalog_card_shows_the_gross_price_and_the_note(theme: str) -> None:
    with use_theme(theme), translation.override("de"):
        html = render_to_string(
            "catalog/includes/product_card.html",
            {"card": card(), "currency": "EUR", "request": request("de")},
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
                "cards": [card(), on_request],
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
    html = render_to_string(
        "catalog/includes/product_card.html",
        {"card": card(fiscal_category="luxury"), "currency": "EUR", "request": request()},
    )
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    assert "12.50" not in text
    assert "Price at checkout" in text


# --- Cart lines (ROUND_HALF_UP per line)


def test_cart_lines_before_the_address_use_the_estimated_rate() -> None:
    with translation.override("de"):
        unit, total = present.line_prices(
            Decimal("0.25"), 3, "standard", vendor=VENDOR, request=request("de")
        )
    # 0.25 + 19 % = 0.2975 -> 0.30; the line 0.75 + 0.1425 -> 0.89 (not 3 x 0.30).
    assert (unit, total) == (Decimal("0.30"), Decimal("0.89"))


def test_cart_lines_after_the_address_use_the_engine_decision() -> None:
    unit, total = present.line_prices(
        Decimal("0.25"),
        3,
        "standard",
        vendor=VENDOR,
        request=request(),
        line_net=Decimal("0.75"),
        tax_rate=Decimal("22.0000"),
        line_tax=Decimal("0.17"),
    )
    assert (unit, total) == (Decimal("0.31"), Decimal("0.92"))


def test_cart_lines_for_a_business_buyer_who_opted_in_are_net() -> None:
    req = request()
    prices.set_business_net_view(req, True)
    assert present.line_prices(Decimal("0.25"), 3, "standard", vendor=VENDOR, request=req) == (
        Decimal("0.25"),
        Decimal("0.75"),
    )
