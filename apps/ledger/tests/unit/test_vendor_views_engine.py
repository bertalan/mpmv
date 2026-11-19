"""VEN.9: periods, payout kinds, the extract reference and CSV safety (pure, no database)."""

import datetime as dt
from decimal import Decimal

import pytest

from apps.ledger import engine


def test_parse_date() -> None:
    assert engine.parse_date("2026-03-05") == dt.date(2026, 3, 5)
    assert engine.parse_date(" 2026-03-05 ") == dt.date(2026, 3, 5)
    assert engine.parse_date("") is None
    assert engine.parse_date("05/03/2026") is None
    assert engine.parse_date("2026-02-30") is None


def test_month_bounds() -> None:
    assert engine.month_bounds(dt.date(2026, 2, 10)) == (dt.date(2026, 2, 1), dt.date(2026, 2, 28))
    assert engine.month_bounds(dt.date(2026, 12, 31)) == (
        dt.date(2026, 12, 1),
        dt.date(2026, 12, 31),
    )


def test_period_end_is_exclusive_the_day_after() -> None:
    start, end = engine.period_bounds(dt.date(2026, 3, 1), dt.date(2026, 3, 31), dt.UTC)
    assert start == dt.datetime(2026, 3, 1, tzinfo=dt.UTC)
    assert end == dt.datetime(2026, 4, 1, tzinfo=dt.UTC)
    assert engine.period_bounds(None, None, dt.UTC) == (None, None)


TODAY = dt.date(2026, 3, 18)


def test_read_period_open_by_default_or_the_month_when_asked() -> None:
    assert engine.read_period("", "", today=TODAY) == engine.Period(None, None, "")
    assert engine.read_period("", "", today=TODAY, default_month=True) == engine.Period(
        dt.date(2026, 3, 1), dt.date(2026, 3, 31), ""
    )


def test_read_period_keeps_a_valid_range() -> None:
    period = engine.read_period("2026-01-01", "2026-01-31", today=TODAY, default_month=True)
    assert period == engine.Period(dt.date(2026, 1, 1), dt.date(2026, 1, 31), "")
    assert engine.read_period("2026-01-01", "", today=TODAY) == engine.Period(
        dt.date(2026, 1, 1), None, ""
    )


@pytest.mark.parametrize(
    ("first", "last", "error"),
    [
        ("01/01/2026", "", engine.PERIOD_BAD_DATE),
        ("", "2026-02-30", engine.PERIOD_BAD_DATE),
        ("2026-02-01", "2026-01-01", engine.PERIOD_REVERSED),
    ],
)
def test_a_bad_period_is_ignored_with_a_reason(first: str, last: str, error: str) -> None:
    assert engine.read_period(first, last, today=TODAY) == engine.Period(None, None, error)
    month = engine.read_period(first, last, today=TODAY, default_month=True)
    assert (month.first, month.last, month.error) == (
        dt.date(2026, 3, 1),
        dt.date(2026, 3, 31),
        error,
    )


def test_a_closed_period_needs_both_ends_so_one_side_falls_back_to_the_month() -> None:
    period = engine.read_period("2026-01-05", "", today=TODAY, default_month=True, closed=True)
    assert (period.first, period.last) == (dt.date(2026, 3, 1), dt.date(2026, 3, 31))
    assert period.error == engine.PERIOD_INCOMPLETE


def test_payout_kinds_are_debits() -> None:
    assert set(engine.PAYOUT_KINDS) == {engine.PAYOUT, engine.TRANSFER}
    assert set(engine.PAYOUT_KINDS) <= engine.DEBIT_KINDS


@pytest.mark.parametrize(
    "cell",
    [
        *("=1+1", "+1", "-1", "@SUM(A1)", " =1", "\t1", "\r1"),
        *("\N{FULLWIDTH EQUALS SIGN}1", "'=1", "\n=1"),
    ],
)
def test_formula_like_text_is_defused(cell: str) -> None:
    assert engine.csv_safe(cell).startswith("'")


@pytest.mark.parametrize("cell", ["", "Payout June", "a=b", "12 EUR", "N-2026-0001"])
def test_plain_text_is_unchanged(cell: str) -> None:
    assert engine.csv_safe(cell) == cell


def test_signed_amounts_are_plain_decimal_strings() -> None:
    assert engine.signed(Decimal("12.5")) == "+12.50"
    assert engine.signed(Decimal("-3")) == "-3.00"
    assert engine.signed(Decimal("0.005")) == "+0.01"  # ROUND_HALF_UP
    with pytest.raises(TypeError):
        engine.signed(1.5)  # type: ignore[arg-type]


def test_the_extract_reference_is_stable_and_names_vendor_and_period() -> None:
    vendor = "0f8fad5b-d9cb-469f-a165-70867728950e"
    ref = engine.extract_reference(vendor, dt.date(2026, 3, 1), dt.date(2026, 3, 31))
    assert ref == "LX-0F8FAD5B-20260301-20260331"
    assert ref == engine.extract_reference(vendor, dt.date(2026, 3, 1), dt.date(2026, 3, 31))
    assert engine.extract_reference(vendor, None, None) == "LX-0F8FAD5B-START-END"
