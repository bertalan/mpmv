"""CORE.29 (Q-14): the retention cutoff of the API mutation log, 12 calendar months back."""

import datetime as dt

from apps.api.engine import months_before

UTC = dt.UTC


def test_twelve_months_before_is_the_same_day_a_year_earlier() -> None:
    now = dt.datetime(2027, 3, 15, 12, 30, tzinfo=UTC)
    assert months_before(now, 12) == dt.datetime(2026, 3, 15, 12, 30, tzinfo=UTC)


def test_a_missing_day_falls_back_to_the_last_day_of_the_month() -> None:
    leap_day = dt.datetime(2028, 2, 29, 8, 0, tzinfo=UTC)
    assert months_before(leap_day, 12) == dt.datetime(2027, 2, 28, 8, 0, tzinfo=UTC)


def test_it_crosses_year_ends() -> None:
    assert months_before(dt.datetime(2027, 1, 31, tzinfo=UTC), 2) == dt.datetime(
        2026, 11, 30, tzinfo=UTC
    )
