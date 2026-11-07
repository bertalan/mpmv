"""CAT.1: pure catalog rules, SKU normalization and price validation (spec 03 §3.2)."""

import datetime as dt
from decimal import Decimal

import pytest

from apps.catalog import engine

JAN = dt.datetime(2027, 1, 1, tzinfo=dt.UTC)
FEB = dt.datetime(2027, 2, 1, tzinfo=dt.UTC)


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [("abc-123", "ABC-123"), ("  Ab_12 ", "AB_12"), ("ABC", "ABC"), ("x9", "X9")],
)
def test_sku_is_stripped_and_upper_cased(raw: str, normalized: str) -> None:
    assert engine.normalize_sku(raw) == normalized


@pytest.mark.parametrize("raw", ["", "   ", "\t\n"])
def test_an_empty_sku_is_refused(raw: str) -> None:
    with pytest.raises(engine.InvalidSku):
        engine.normalize_sku(raw)


@pytest.mark.parametrize(
    ("raw", "price"),
    [
        (Decimal("12.5"), Decimal("12.50")),
        ("19.99", Decimal("19.99")),
        (10, Decimal("10.00")),
        ("0", Decimal("0.00")),
    ],
)
def test_prices_are_exact_decimals_with_two_places(raw: object, price: Decimal) -> None:
    result = engine.to_price(raw)
    assert result == price
    assert result.as_tuple().exponent == -2


def test_float_prices_are_refused() -> None:
    with pytest.raises(TypeError):
        engine.to_price(12.5)


def test_prices_with_more_than_two_decimals_are_refused_not_rounded() -> None:
    with pytest.raises(ValueError, match="2 decimals"):
        engine.to_price("10.005")


def test_negative_prices_are_refused() -> None:
    with pytest.raises(ValueError, match="negative"):
        engine.to_price("-0.01")


def test_a_regular_price_without_a_sale_is_valid() -> None:
    assert engine.price_errors(Decimal("10.00"), None, None, None) == {}


def test_a_sale_price_below_the_regular_price_in_a_valid_period_is_valid() -> None:
    assert engine.price_errors(Decimal("10.00"), Decimal("8.00"), JAN, FEB) == {}


@pytest.mark.parametrize("sale", [Decimal("10.00"), Decimal("12.00")])
def test_a_sale_price_must_be_below_the_regular_price(sale: Decimal) -> None:
    errors = engine.price_errors(Decimal("10.00"), sale, None, None)
    assert set(errors) == {"sale_price_net"}


@pytest.mark.parametrize("end", [JAN, JAN - dt.timedelta(days=1)])
def test_a_sale_must_end_after_it_starts(end: dt.datetime) -> None:
    errors = engine.price_errors(Decimal("10.00"), Decimal("8.00"), JAN, end)
    assert set(errors) == {"sale_to"}


def test_a_sale_period_without_a_sale_price_is_refused() -> None:
    errors = engine.price_errors(Decimal("10.00"), None, JAN, FEB)
    assert set(errors) == {"sale_price_net"}
