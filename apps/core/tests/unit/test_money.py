"""Money rules (spec 01 conventions): Decimal only, ROUND_HALF_UP per line, 4 decimals inside."""

from decimal import Decimal

import pytest

from apps.core.money import CURRENCY, round_internal, round_line, sum_lines, to_decimal


def test_currency_is_explicit_eur() -> None:
    assert CURRENCY == "EUR"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0.005", "0.01"),
        ("2.675", "2.68"),  # binary floats would give 2.67; Decimal must not
        ("9.3424", "9.34"),
        ("6.875", "6.88"),
        ("-0.005", "-0.01"),  # half away from zero for refunds too
        ("10", "10.00"),
    ],
)
def test_round_line_is_half_up_to_cents(raw: str, expected: str) -> None:
    assert round_line(Decimal(raw)) == Decimal(expected)
    assert str(round_line(Decimal(raw))) == expected


def test_round_internal_keeps_four_decimals() -> None:
    assert round_internal(Decimal("9.34245")) == Decimal("9.3425")
    assert round_internal(Decimal("1")) == Decimal("1.0000")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("12.30", Decimal("12.30")),
        (" 7 ", Decimal("7")),
        (12, Decimal("12")),
        (Decimal("1.5"), Decimal("1.5")),
    ],
)
def test_to_decimal_accepts_exact_inputs(value: object, expected: Decimal) -> None:
    assert to_decimal(value) == expected


def test_to_decimal_rejects_floats() -> None:
    with pytest.raises(TypeError, match="float"):
        to_decimal(12.3)


def test_to_decimal_rejects_bools_and_garbage() -> None:
    with pytest.raises(TypeError):
        to_decimal(True)
    with pytest.raises(ValueError, match="not a decimal"):
        to_decimal("12,30")


def test_sum_lines_rounds_each_line_before_adding() -> None:
    # Three lines of 0.005 each: per-line rounding gives 0.03, rounding the total would give 0.02.
    lines = [Decimal("0.005"), Decimal("0.005"), Decimal("0.005")]
    assert sum_lines(lines) == Decimal("0.03")


def test_sum_lines_of_nothing_is_zero_cents() -> None:
    assert sum_lines([]) == Decimal("0.00")
