"""VEN.20 slice 2: when the neutral closure notice shows (spec 04 case 10)."""

import datetime as dt

from apps.shipping.engine import ClosureNotice, ClosureRule, closure_notice_for

D = dt.date
TODAY = D(2027, 7, 22)


def once(a: dt.date, b: dt.date, days: int = 14) -> tuple[ClosureRule, int]:
    return ClosureRule(a, b, repeats_yearly=False), days


def test_a_closure_starting_in_10_days_with_14_days_notice_shows() -> None:
    notice = closure_notice_for([once(D(2027, 8, 1), D(2027, 8, 31))], TODAY)
    assert notice == ClosureNotice(D(2027, 8, 1), D(2027, 8, 31), D(2027, 9, 1))


def test_a_closure_starting_in_20_days_shows_nothing_yet() -> None:
    assert closure_notice_for([once(D(2027, 8, 11), D(2027, 8, 31))], TODAY) is None


def test_the_notice_shows_on_the_first_day_of_the_window_and_during_the_closure() -> None:
    rules = [once(D(2027, 8, 5), D(2027, 8, 10))]
    assert closure_notice_for(rules, D(2027, 7, 22)) is not None
    assert closure_notice_for(rules, D(2027, 7, 21)) is None
    assert closure_notice_for(rules, D(2027, 8, 10)) is not None
    assert closure_notice_for(rules, D(2027, 8, 11)) is None


def test_adjacent_ranges_show_as_one_merged_period() -> None:
    rules = [once(D(2027, 8, 1), D(2027, 8, 15)), once(D(2027, 8, 16), D(2027, 8, 31), 0)]
    notice = closure_notice_for(rules, TODAY)
    assert notice == ClosureNotice(D(2027, 8, 1), D(2027, 8, 31), D(2027, 9, 1))


def test_a_yearly_closure_across_the_year_end() -> None:
    rule = (ClosureRule(D(2026, 12, 24), D(2027, 1, 6), repeats_yearly=True), 14)
    notice = closure_notice_for([rule], D(2027, 12, 20))
    assert notice == ClosureNotice(D(2027, 12, 24), D(2028, 1, 6), D(2028, 1, 7))


def test_a_zero_day_notice_shows_only_during_the_closure() -> None:
    rules = [once(D(2027, 8, 1), D(2027, 8, 5), 0)]
    assert closure_notice_for(rules, D(2027, 7, 31)) is None
    assert closure_notice_for(rules, D(2027, 8, 1)) is not None
