"""TAX.12: consumer prices include VAT (Directive 98/6/EC, owner decision MQ-12), the pure part.

The rate is the destination country's, decided by the TAX.3 engine for a consumer; the country
is the delivery country once the buyer gave one, else an estimate. Gross amounts are rounded
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
        ("DE", engine.STANDARD): Decimal("19.0000"),
        ("FR", engine.STANDARD): Decimal("20.0000"),
        ("AT", engine.STANDARD): Decimal("20.0000"),
    }
)
ON = dt.date(2027, 6, 1)


def decide(country: str, category: str = engine.STANDARD, **kwargs: Any) -> engine.TaxDecision:
    return engine.consumer_tax_decision(
        country, category, rates=RATES, on=ON, platform_country="AT", **kwargs
    )


@pytest.mark.parametrize(
    ("country", "rate"),
    [("IT", Decimal("22.0000")), ("DE", Decimal("19.0000")), ("FR", Decimal("20.0000"))],
)
def test_a_consumer_gets_the_destination_rate(country: str, rate: Decimal) -> None:
    decision = decide(country)
    assert decision.rate == rate
    assert decision.country_of_taxation == country


def test_the_platform_country_does_not_change_the_destination_rate() -> None:
    assert decide("IT").rate == decide("IT", territories=()).rate == Decimal("22.0000")
    assert engine.consumer_tax_decision(
        "IT", engine.STANDARD, rates=RATES, on=ON, platform_country="IT"
    ).rate == Decimal("22.0000")


def test_the_category_picks_the_rate() -> None:
    assert decide("IT", engine.REDUCED).rate == Decimal("10.0000")


def test_a_destination_outside_the_eu_is_an_export_at_zero() -> None:
    decision = decide("CH")
    assert decision.rate == Decimal(0)
    assert decision.reason == engine.REASON_EXPORT


def test_a_missing_rate_is_an_error_never_zero() -> None:
    with pytest.raises(engine.MissingTaxRate):
        decide("ES")


def test_an_unknown_category_is_refused() -> None:
    with pytest.raises(ValueError, match="fiscal category"):
        decide("IT", "book_art74")


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
