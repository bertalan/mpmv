"""VEN.9: periods, payout kinds and CSV safety (pure, no database)."""

import datetime as dt
from decimal import Decimal

import pytest

from apps.ledger import engine


def test_parse_date() -> None:
    assert engine.parse_date("2026-03-05") == dt.date(2026, 3, 5)
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


@pytest.mark.parametrize(
    "cell", ["=1+1", "+1", "-1", "@SUM(A1)", " =1", "\t1", "\r1", "\uff1d1", "'=1"]
)
def test_formula_like_text_is_defused(cell: str) -> None:
    assert engine.csv_safe(cell).startswith("'")


@pytest.mark.parametrize("cell", ["", "Payout June", "a=b", "12 EUR"])
def test_plain_text_is_unchanged(cell: str) -> None:
    assert engine.csv_safe(cell) == cell


def test_signed_amounts() -> None:
    assert engine.signed(Decimal("12.5")) == "+12.50"
    assert engine.signed(Decimal("-3")) == "-3.00"
