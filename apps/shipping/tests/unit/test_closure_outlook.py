"""VEN.20: one reading of a vendor's rules gives both the notice and the dispatch date, and a
chain of closures longer than the first look-ahead is followed to its end."""

import datetime as dt

from apps.shipping.engine import (
    NOTICE_LOOKAHEAD_DAYS,
    ClosureNotice,
    ClosureOutlook,
    ClosureRule,
    closure_outlook,
    dispatch_date_for,
)

D = dt.date


def once(a: dt.date, b: dt.date) -> ClosureRule:
    return ClosureRule(a, b, repeats_yearly=False)


def test_an_open_vendor_dispatches_today_and_shows_no_notice() -> None:
    assert closure_outlook([], D(2027, 8, 5)) == ClosureOutlook(None, D(2027, 8, 5))


def test_the_notice_and_the_dispatch_date_agree_during_a_closure() -> None:
    rules = [(once(D(2027, 8, 1), D(2027, 8, 15)), 14), (once(D(2027, 8, 10), D(2027, 8, 31)), 14)]
    outlook = closure_outlook(rules, D(2027, 8, 5))
    assert outlook == ClosureOutlook(
        ClosureNotice(D(2027, 8, 1), D(2027, 8, 31), D(2027, 9, 1)), D(2027, 9, 1)
    )


def test_a_coming_closure_shows_its_notice_but_the_package_leaves_today() -> None:
    outlook = closure_outlook([(once(D(2027, 8, 1), D(2027, 8, 31)), 14)], D(2027, 7, 22))
    assert outlook.dispatch_on == D(2027, 7, 22)
    assert outlook.notice == ClosureNotice(D(2027, 8, 1), D(2027, 8, 31), D(2027, 9, 1))


def test_a_chain_that_runs_past_the_look_ahead_is_followed_to_its_end() -> None:
    today = D(2027, 1, 10)
    edge = today + dt.timedelta(days=NOTICE_LOOKAHEAD_DAYS)
    # The second range starts after the first window ends but touches the first one.
    rules = [
        once(D(2027, 1, 1), edge + dt.timedelta(days=10)),
        once(edge + dt.timedelta(days=11), edge + dt.timedelta(days=40)),
    ]
    assert dispatch_date_for(rules, today) == edge + dt.timedelta(days=41)
    outlook = closure_outlook([(rule, 14) for rule in rules], today)
    assert outlook.dispatch_on == edge + dt.timedelta(days=41)
    assert outlook.notice == ClosureNotice(
        D(2027, 1, 1), edge + dt.timedelta(days=40), edge + dt.timedelta(days=41)
    )


def test_a_closure_without_end_stops_at_the_cap() -> None:
    """Two yearly ranges that together cover the whole year never open: the search stops."""
    rules = [
        ClosureRule(D(2027, 1, 1), D(2027, 6, 30), repeats_yearly=True),
        ClosureRule(D(2027, 7, 1), D(2027, 12, 31), repeats_yearly=True),
    ]
    found = dispatch_date_for(rules, D(2027, 3, 1))
    assert D(2036, 1, 1) <= found <= D(2040, 1, 1)
