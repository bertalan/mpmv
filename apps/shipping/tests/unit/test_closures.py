"""VEN.20: dispatch date and delivery window from closed periods (spec 04 cases 8-9)."""

import datetime as dt

import pytest

from apps.shipping.engine import ClosedPeriod, delivery_window, earliest_dispatch_date

D = dt.date


def period(a: dt.date, b: dt.date) -> ClosedPeriod:
    return ClosedPeriod(a, b)


AUG_OVERLAP = [period(D(2027, 8, 1), D(2027, 8, 15)), period(D(2027, 8, 10), D(2027, 8, 31))]
AUG_ADJACENT = [period(D(2027, 8, 1), D(2027, 8, 15)), period(D(2027, 8, 16), D(2027, 8, 31))]


def test_case_8_overlapping_closures_move_the_dispatch_date() -> None:
    assert earliest_dispatch_date(D(2027, 8, 5), AUG_OVERLAP) == D(2027, 9, 1)


def test_case_8_adjacent_closures_merge() -> None:
    assert earliest_dispatch_date(D(2027, 8, 5), AUG_ADJACENT) == D(2027, 9, 1)
    assert earliest_dispatch_date(D(2027, 8, 16), AUG_ADJACENT) == D(2027, 9, 1)


def test_case_8_outside_the_closure_nothing_moves() -> None:
    assert earliest_dispatch_date(D(2027, 7, 31), AUG_OVERLAP) == D(2027, 7, 31)
    assert earliest_dispatch_date(D(2027, 9, 1), AUG_OVERLAP) == D(2027, 9, 1)


def test_first_and_last_day_are_closed_days() -> None:
    closed = [period(D(2027, 8, 1), D(2027, 8, 15))]
    assert earliest_dispatch_date(D(2027, 8, 1), closed) == D(2027, 8, 16)
    assert earliest_dispatch_date(D(2027, 8, 15), closed) == D(2027, 8, 16)


def test_a_gap_of_one_day_does_not_merge() -> None:
    closed = [period(D(2027, 8, 1), D(2027, 8, 10)), period(D(2027, 8, 12), D(2027, 8, 20))]
    assert earliest_dispatch_date(D(2027, 8, 5), closed) == D(2027, 8, 11)


def test_no_periods_and_unsorted_input() -> None:
    assert earliest_dispatch_date(D(2027, 8, 5), []) == D(2027, 8, 5)
    closed = [period(D(2027, 8, 10), D(2027, 8, 20)), period(D(2027, 8, 1), D(2027, 8, 9))]
    assert earliest_dispatch_date(D(2027, 8, 1), closed) == D(2027, 8, 21)


def test_a_chain_of_adjacent_periods_merges_end_to_end() -> None:
    closed = [period(D(2027, 8, 1), D(2027, 8, 5)), period(D(2027, 8, 6), D(2027, 8, 9))]
    closed.append(period(D(2027, 8, 10), D(2027, 8, 12)))
    assert earliest_dispatch_date(D(2027, 8, 3), closed) == D(2027, 8, 13)


def test_delivery_window_counts_from_the_dispatch_date() -> None:
    assert delivery_window(D(2027, 9, 1), 2, 5) == (D(2027, 9, 3), D(2027, 9, 6))
    assert delivery_window(D(2027, 12, 30), 0, 3) == (D(2027, 12, 30), D(2028, 1, 2))


@pytest.mark.parametrize(("low", "high"), [(-1, 3), (4, 3)])
def test_delivery_window_refuses_inverted_or_negative_days(low: int, high: int) -> None:
    with pytest.raises(ValueError, match="delivery days"):
        delivery_window(D(2027, 9, 1), low, high)
