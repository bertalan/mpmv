"""TAX.12: consumer prices include VAT (Directive 98/6/EC, owner decisions MQ-12 and "il prezzo
reale", 2026-11-16), the pure part.

A displayed price is what checkout will charge: `resolve_goods_tax` on a consumer context with the
vendor's own regime, at the delivery (or estimated) destination. Gross amounts are rounded
ROUND_HALF_UP to the cent per line, never with floats.
"""

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from apps.tax import engine

RATES = engine.RateTable(
    {
        ("IT", engine.STANDARD): Decimal("22.0000"),
        ("IT", engine.REDUCED): Decimal("10.0000"),
        ("IT", engine.BOOKS): Decimal("4.0000"),
        ("IT", engine.ZERO): Decimal("0.0000"),
        ("DE", engine.STANDARD): Decimal("19.0000"),
        ("FR", engine.STANDARD): Decimal("20.0000"),
        ("AT", engine.STANDARD): Decimal("20.0000"),
        ("GB", engine.STANDARD): Decimal("20.0000"),
    }
)
TERRITORIES = (engine.territory_rule("IT-LIVIGNO", "IT", engine.OUTSIDE_VAT_AREA, ["23041"]),)
ON = dt.date(2027, 6, 1)
OSS = engine.VendorTaxProfileSnapshot(regime=engine.OSS)
DOMESTIC = engine.VendorTaxProfileSnapshot(regime=engine.DOMESTIC_ONLY)
FORFETTARIO = engine.VendorTaxProfileSnapshot(
    regime=engine.EXEMPT, exempt_reason=engine.FLAT_RATE_FORFETTARIO
)
ART74 = engine.VendorTaxProfileSnapshot(regime=engine.EXEMPT, exempt_reason=engine.ART74_PUBLISHING)


def decide(
    country: str,
    category: str = engine.STANDARD,
    *,
    profile: engine.VendorTaxProfileSnapshot = OSS,
    vendor_country: str = "IT",
    postcode: str | None = None,
    **kwargs: Any,
) -> engine.TaxDecision:
    ctx = engine.consumer_context(
        vendor_country,
        profile,
        destination_country=country,
        destination_postcode=postcode,
        rates=RATES,
        on=ON,
        platform_country="AT",
        territories=TERRITORIES,
        **kwargs,
    )
    return engine.resolve_goods_tax(ctx, category)


@pytest.mark.parametrize(
    ("country", "rate"),
    [("IT", Decimal("22.0000")), ("DE", Decimal("19.0000")), ("FR", Decimal("20.0000"))],
)
def test_an_oss_vendor_charges_a_consumer_the_destination_rate(country: str, rate: Decimal) -> None:
    decision = decide(country)
    assert decision.rate == rate
    assert decision.country_of_taxation == country


def test_a_domestic_only_vendor_seen_from_germany_charges_its_own_rate() -> None:
    assert decide("DE", profile=DOMESTIC).rate == Decimal("22.0000")


@pytest.mark.parametrize("profile", [FORFETTARIO, ART74])
@pytest.mark.parametrize("country", ["IT", "DE", "FR"])
def test_an_exempt_vendor_charges_no_vat_anywhere(
    profile: engine.VendorTaxProfileSnapshot, country: str
) -> None:
    assert decide(country, profile=profile).rate == Decimal(0)


def test_the_category_picks_the_rate() -> None:
    assert decide("IT", engine.REDUCED).rate == Decimal("10.0000")


def test_a_zero_rated_category_adds_nothing() -> None:
    decision = decide("IT", engine.ZERO)
    assert decision.rate == Decimal(0)
    assert engine.gross_amount(Decimal("12.50"), decision.rate) == Decimal("12.50")


def test_the_catalog_book_category_is_the_books_rate() -> None:
    assert engine.tax_category("book_art74") == engine.BOOKS
    assert engine.tax_category(engine.STANDARD) == engine.STANDARD
    assert decide("IT", "book_art74").rate == Decimal("4.0000")
    # The art. 74 treatment comes from the vendor's exempt profile, not from the category.
    assert decide("IT", "book_art74", profile=ART74).rate == Decimal(0)


def test_a_destination_outside_the_eu_is_an_export_at_zero() -> None:
    decision = decide("CH")
    assert decision.rate == Decimal(0)
    assert decision.reason == engine.REASON_EXPORT


def test_a_territory_outside_the_vat_area_is_zero() -> None:
    decision = decide("IT", postcode="23041")  # Livigno
    assert decision.rate == Decimal(0)
    assert decision.reason == engine.OUTSIDE_VAT_TERRITORY


def test_northern_ireland_follows_the_xi_rules() -> None:
    assert engine.is_northern_ireland("GB", "BT1 1AA")
    assert decide("GB", postcode="BT1 1AA").rate == Decimal("20.0000")  # OSS: UK rate
    assert decide("GB", postcode="BT1 1AA", profile=DOMESTIC).rate == Decimal("22.0000")


def test_a_missing_rate_is_an_error_never_zero() -> None:
    with pytest.raises(engine.MissingTaxRate):
        decide("ES")


def test_an_unknown_category_is_refused() -> None:
    with pytest.raises(ValueError, match="fiscal category"):
        decide("IT", "luxury")


@pytest.mark.parametrize(
    ("net", "rate", "gross"),
    [
        (Decimal("10.00"), Decimal("22.0000"), Decimal("12.20")),
        (Decimal("12.50"), Decimal("19.0000"), Decimal("14.88")),  # 2.375 -> 2.38
        (Decimal("0.25"), Decimal("22.0000"), Decimal("0.31")),  # 0.055 -> 0.06, half up
        (Decimal("0.05"), Decimal("10.0000"), Decimal("0.06")),  # 0.005 -> 0.01, half up
        (Decimal("99.99"), Decimal("0"), Decimal("99.99")),
    ],
)
def test_the_gross_amount_is_rounded_half_up_per_line(
    net: Decimal, rate: Decimal, gross: Decimal
) -> None:
    assert engine.gross_amount(net, rate) == gross


def test_the_gross_amount_refuses_floats() -> None:
    with pytest.raises(TypeError):
        engine.gross_amount(10.0, Decimal("22"))  # type: ignore[arg-type]


def test_the_estimated_country_prefers_the_delivery_country() -> None:
    assert engine.estimate_price_country(
        delivery_country="fr", language="de", operator_country="IT"
    ) == ("FR", False)


def test_the_estimated_country_falls_back_to_the_language_then_the_operator() -> None:
    assert engine.estimate_price_country(
        delivery_country="", language="de", operator_country="IT"
    ) == ("DE", True)
    assert engine.estimate_price_country(
        delivery_country="", language="fr-be", operator_country="IT"
    ) == ("FR", True)
    # English names no single country: the operator's country.
    assert engine.estimate_price_country(
        delivery_country="", language="en", operator_country="IT"
    ) == ("IT", True)


def test_an_invalid_delivery_country_is_ignored() -> None:
    assert engine.estimate_price_country(
        delivery_country="??", language="es", operator_country="IT"
    ) == ("ES", True)


def test_the_five_site_languages_have_a_default_country_or_the_operator() -> None:
    assert engine.LANGUAGE_PRICE_COUNTRIES == {"it": "IT", "de": "DE", "fr": "FR", "es": "ES"}
