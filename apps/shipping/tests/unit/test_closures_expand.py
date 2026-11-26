"""VEN.20: expanding closure rules into closed periods (spec 04 case 9 and edge cases)."""

import datetime as dt

from apps.shipping.engine import (
    ClosedPeriod,
    ClosureRule,
    earliest_dispatch_date,
    expand_closures,
)

D = dt.date


def yearly(a: dt.date, b: dt.date) -> ClosureRule:
    return ClosureRule(a, b, repeats_yearly=True)


def once(a: dt.date, b: dt.date) -> ClosureRule:
    return ClosureRule(a, b, repeats_yearly=False)


def dispatch(rules: list[ClosureRule], ordered_on: dt.date) -> dt.date:
    window = expand_closures(
        rules, ordered_on - dt.timedelta(days=400), ordered_on + dt.timedelta(days=180)
    )
    return earliest_dispatch_date(ordered_on, window)


def test_case_9_yearly_closure_across_the_year_end() -> None:
    rules = [yearly(D(2027, 12, 24), D(2028, 1, 6))]
    assert dispatch(rules, D(2028, 1, 2)) == D(2028, 1, 7)
    assert dispatch(rules, D(2027, 12, 20)) == D(2027, 12, 20)
    assert dispatch(rules, D(2028, 1, 7)) == D(2028, 1, 7)


def test_a_yearly_closure_recurs_in_later_years_but_not_before_it_started() -> None:
    rules = [yearly(D(2027, 12, 24), D(2028, 1, 6))]
    assert dispatch(rules, D(2030, 12, 31)) == D(2031, 1, 7)
    assert dispatch(rules, D(2027, 1, 2)) == D(2027, 1, 2)


def test_a_one_off_closure_does_not_recur() -> None:
    rules = [once(D(2027, 8, 1), D(2027, 8, 15))]
    assert dispatch(rules, D(2028, 8, 5)) == D(2028, 8, 5)
    assert dispatch(rules, D(2027, 8, 5)) == D(2027, 8, 16)


def test_a_yearly_closure_in_a_later_year_is_expanded_inside_the_window() -> None:
    rules = [yearly(D(2025, 8, 1), D(2025, 8, 15))]
    assert expand_closures(rules, D(2027, 7, 1), D(2027, 9, 30)) == (
        ClosedPeriod(D(2027, 8, 1), D(2027, 8, 15)),
    )


def test_periods_overlapping_the_window_edge_are_kept_whole() -> None:
    rules = [yearly(D(2027, 12, 24), D(2028, 1, 6))]
    assert expand_closures(rules, D(2028, 1, 3), D(2028, 1, 4)) == (
        ClosedPeriod(D(2027, 12, 24), D(2028, 1, 6)),
    )


def test_a_window_that_misses_every_period_is_empty() -> None:
    rules = [once(D(2027, 8, 1), D(2027, 8, 15)), yearly(D(2027, 12, 24), D(2028, 1, 6))]
    assert expand_closures(rules, D(2027, 9, 1), D(2027, 11, 30)) == ()


def test_a_window_that_ends_before_it_starts_is_empty() -> None:
    rules = [yearly(D(2027, 8, 1), D(2027, 8, 15))]
    assert expand_closures(rules, D(2027, 9, 1), D(2027, 8, 1)) == ()


def test_a_leap_day_inside_a_yearly_range_is_part_of_leap_years_only() -> None:
    rules = [yearly(D(2027, 2, 20), D(2027, 3, 5))]
    assert expand_closures(rules, D(2028, 2, 1), D(2028, 3, 31)) == (
        ClosedPeriod(D(2028, 2, 20), D(2028, 3, 5)),
    )
    assert dispatch(rules, D(2028, 2, 29)) == D(2028, 3, 6)


def test_a_yearly_range_ending_on_a_leap_day_is_clamped_in_other_years() -> None:
    rules = [yearly(D(2028, 2, 20), D(2028, 2, 29))]
    assert expand_closures(rules, D(2029, 2, 1), D(2029, 3, 31)) == (
        ClosedPeriod(D(2029, 2, 20), D(2029, 2, 28)),
    )
    assert expand_closures(rules, D(2032, 2, 1), D(2032, 3, 31)) == (
        ClosedPeriod(D(2032, 2, 20), D(2032, 2, 29)),
    )


def test_overlapping_rules_are_returned_sorted_and_merged_at_dispatch() -> None:
    rules = [once(D(2027, 8, 10), D(2027, 8, 31)), once(D(2027, 8, 1), D(2027, 8, 15))]
    periods = expand_closures(rules, D(2027, 8, 1), D(2027, 8, 31))
    assert [p.start for p in periods] == [D(2027, 8, 1), D(2027, 8, 10)]
    assert dispatch(rules, D(2027, 8, 5)) == D(2027, 9, 1)
