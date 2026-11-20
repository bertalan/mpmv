"""FIT.5 (3/3): the pure date rules of the 24-month inactivity cleanup (spec 17 §17.4.5)."""

import datetime as dt

from apps.fitment import engine

UTC = dt.UTC
USED = dt.datetime(2024, 1, 31, 12, 0, tzinfo=UTC)


def test_add_months_clamps_to_the_last_day_of_the_month() -> None:
    assert engine.add_months(USED, 1) == dt.datetime(2024, 2, 29, 12, 0, tzinfo=UTC)
    assert engine.add_months(USED, 24) == dt.datetime(2026, 1, 31, 12, 0, tzinfo=UTC)


def test_nothing_happens_while_the_profile_is_recent() -> None:
    now = USED + dt.timedelta(days=600)
    assert engine.cleanup_action(USED, None, now) == engine.KEEP


def test_the_notice_goes_out_thirty_days_before_the_limit() -> None:
    limit = engine.add_months(USED, 24)
    assert engine.cleanup_action(USED, None, limit - dt.timedelta(days=31)) == engine.KEEP
    assert engine.cleanup_action(USED, None, limit - dt.timedelta(days=30)) == engine.NOTIFY


def test_a_profile_is_deleted_only_after_the_limit_and_the_notice_period() -> None:
    limit = engine.add_months(USED, 24)
    noticed = limit - dt.timedelta(days=30)
    assert engine.cleanup_action(USED, noticed, limit - dt.timedelta(seconds=1)) == engine.KEEP
    assert engine.cleanup_action(USED, noticed, limit) == engine.DELETE


def test_a_late_notice_still_gets_the_full_notice_period() -> None:
    limit = engine.add_months(USED, 24)
    noticed = limit + dt.timedelta(days=10)  # the job was down
    assert engine.cleanup_action(USED, noticed, limit + dt.timedelta(days=39)) == engine.KEEP
    assert engine.cleanup_action(USED, noticed, limit + dt.timedelta(days=40)) == engine.DELETE
    assert engine.cleanup_action(USED, None, limit + dt.timedelta(days=10)) == engine.NOTIFY


def test_a_notice_older_than_the_last_use_does_not_count() -> None:
    old_notice = USED - dt.timedelta(days=5)
    limit = engine.add_months(USED, 24)
    assert engine.cleanup_action(USED, old_notice, limit + dt.timedelta(days=40)) == engine.NOTIFY
