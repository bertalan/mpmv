"""Validity periods of configuration rows: [valid_from, valid_to), open end allowed (spec 01)."""

import datetime as dt
from dataclasses import dataclass

import pytest

from apps.core.validity import (
    NoValidRow,
    OverlappingValidity,
    covers,
    next_start_error,
    pick_valid,
)

JAN = dt.datetime(2027, 1, 1, tzinfo=dt.UTC)
FEB = dt.datetime(2027, 2, 1, tzinfo=dt.UTC)
MAR = dt.datetime(2027, 3, 1, tzinfo=dt.UTC)


@dataclass(frozen=True)
class Row:
    name: str
    valid_from: dt.datetime
    valid_to: dt.datetime | None


def test_period_includes_its_start_and_excludes_its_end() -> None:
    row = Row("a", JAN, FEB)
    assert covers(row, JAN)
    assert covers(row, FEB - dt.timedelta(microseconds=1))
    assert not covers(row, FEB)
    assert not covers(row, JAN - dt.timedelta(microseconds=1))


def test_open_ended_period_covers_everything_after_its_start() -> None:
    assert covers(Row("a", JAN, None), dt.datetime(2099, 1, 1, tzinfo=dt.UTC))


def test_pick_valid_returns_the_single_row_covering_the_instant() -> None:
    rows = [Row("old", JAN, FEB), Row("new", FEB, None)]
    assert pick_valid(rows, JAN, what="Thing").name == "old"
    assert pick_valid(rows, MAR, what="Thing").name == "new"


def test_pick_valid_without_a_covering_row_is_a_clear_error() -> None:
    with pytest.raises(NoValidRow, match=r"No Thing is valid at 2027-01-01"):
        pick_valid([Row("later", FEB, None)], JAN, what="Thing")


def test_pick_valid_with_overlapping_rows_is_a_clear_error() -> None:
    rows = [Row("a", JAN, None), Row("b", FEB, None)]
    with pytest.raises(OverlappingValidity, match=r"2 Thing rows are valid at 2027-03-01"):
        pick_valid(rows, MAR, what="Thing")


def test_both_errors_are_lookup_errors() -> None:
    assert issubclass(NoValidRow, LookupError)
    assert issubclass(OverlappingValidity, LookupError)


def test_a_first_row_may_start_any_time() -> None:
    assert next_start_error(JAN, None) is None


def test_a_new_row_starts_after_the_latest_start() -> None:
    assert next_start_error(FEB, Row("current", JAN, None)) is None
    error = next_start_error(JAN, Row("current", JAN, None))
    assert error == "Must be after 2027-01-01T00:00:00+00:00, the latest start."


def test_a_new_row_never_shortens_an_already_closed_row() -> None:
    error = next_start_error(FEB, Row("closed", JAN, MAR))
    assert error == "Must not be before the latest end, 2027-03-01T00:00:00+00:00."
    assert next_start_error(MAR, Row("closed", JAN, MAR)) is None
