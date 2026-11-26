"""VEN.20: validation of a vendor closure range (spec 04, holiday and closure schedule)."""

import datetime as dt

from apps.vendors.engine import closure_errors

D = dt.date


def errors(start: dt.date, end: dt.date, *, yearly: bool = False, notice: int = 14) -> set[str]:
    return set(closure_errors(start, end, repeats_yearly=yearly, notice_days=notice))


def test_a_plain_range_is_valid() -> None:
    assert errors(D(2027, 8, 1), D(2027, 8, 15)) == set()


def test_a_single_day_is_valid() -> None:
    assert errors(D(2027, 8, 1), D(2027, 8, 1)) == set()


def test_the_end_cannot_precede_the_start() -> None:
    assert errors(D(2027, 8, 2), D(2027, 8, 1)) == {"end_date"}


def test_notice_days_run_from_0_to_60() -> None:
    assert errors(D(2027, 8, 1), D(2027, 8, 2), notice=0) == set()
    assert errors(D(2027, 8, 1), D(2027, 8, 2), notice=60) == set()
    assert errors(D(2027, 8, 1), D(2027, 8, 2), notice=61) == {"notice_days"}
    assert errors(D(2027, 8, 1), D(2027, 8, 2), notice=-1) == {"notice_days"}


def test_case_9_a_yearly_range_may_cross_the_year_end() -> None:
    assert errors(D(2027, 12, 24), D(2028, 1, 6), yearly=True) == set()


def test_case_9_a_yearly_range_never_starts_or_ends_on_29_february() -> None:
    assert errors(D(2028, 2, 29), D(2028, 3, 5), yearly=True) == {"start_date"}
    assert errors(D(2028, 2, 20), D(2028, 2, 29), yearly=True) == {"end_date"}


def test_a_one_off_range_may_use_29_february() -> None:
    assert errors(D(2028, 2, 29), D(2028, 3, 5)) == set()


def test_a_yearly_range_lasts_less_than_one_year() -> None:
    assert errors(D(2027, 3, 1), D(2028, 2, 28), yearly=True) == set()
    assert errors(D(2027, 3, 1), D(2028, 3, 1), yearly=True) == {"end_date"}
    assert errors(D(2027, 3, 1), D(2029, 1, 1), yearly=True) == {"end_date"}


def test_a_one_off_range_may_last_longer_than_a_year() -> None:
    assert errors(D(2027, 3, 1), D(2029, 1, 1)) == set()
