"""CMS.9: the launch countdown rules (spec 10 §10.2). Pure functions, no database.

`countdown_state(target, now)` decides what the block shows: the remaining days, hours and
minutes before the target, the expired state from the target on. Minutes round up, so the
counter never reads "0 minutes" before the launch. `capped_max_age` keeps any cache of a page
with an active countdown from holding it past the target.
"""

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from apps.cms.engine import CountdownState, capped_max_age, countdown_state

UTC = dt.UTC
ROME = ZoneInfo("Europe/Rome")
NEW_YORK = ZoneInfo("America/New_York")
TARGET = dt.datetime(2026, 12, 1, 9, 0, tzinfo=UTC)  # 10:00 CET


def test_before_the_target_the_remaining_days_hours_and_minutes_are_given() -> None:
    now = TARGET - dt.timedelta(days=1, hours=2, minutes=3)
    assert countdown_state(TARGET, now) == CountdownState(expired=False, days=1, hours=2, minutes=3)


def test_minutes_round_up_so_the_counter_never_reads_zero_before_the_target() -> None:
    assert countdown_state(TARGET, TARGET - dt.timedelta(seconds=30)) == CountdownState(
        expired=False, days=0, hours=0, minutes=1
    )
    one_hour_and_a_second = TARGET - dt.timedelta(hours=1, seconds=1)
    assert countdown_state(TARGET, one_hour_and_a_second) == CountdownState(
        expired=False, days=0, hours=1, minutes=1
    )
    assert countdown_state(TARGET, TARGET - dt.timedelta(microseconds=1)).expired is False


def test_exactly_at_the_target_the_countdown_has_expired() -> None:
    assert countdown_state(TARGET, TARGET) == CountdownState(
        expired=True, days=0, hours=0, minutes=0
    )


def test_after_the_target_the_countdown_stays_expired() -> None:
    assert countdown_state(TARGET, TARGET + dt.timedelta(days=400)).expired is True


def test_the_same_instant_in_different_time_zones_gives_the_same_state() -> None:
    target_in_rome = dt.datetime(2026, 12, 1, 10, 0, tzinfo=ROME)
    assert target_in_rome == TARGET
    now_in_new_york = dt.datetime(2026, 12, 1, 3, 30, tzinfo=NEW_YORK)  # 08:30 UTC
    assert countdown_state(target_in_rome, now_in_new_york) == CountdownState(
        expired=False, days=0, hours=0, minutes=30
    )
    assert countdown_state(target_in_rome, now_in_new_york + dt.timedelta(minutes=30)).expired


def test_a_daylight_saving_change_counts_real_elapsed_time() -> None:
    # Rome leaves summer time on 25 October 2026 at 03:00: that day lasts 25 hours.
    before = dt.datetime(2026, 10, 25, 0, 0, tzinfo=ROME)
    after = dt.datetime(2026, 10, 26, 0, 0, tzinfo=ROME)
    assert countdown_state(after, before) == CountdownState(
        expired=False, days=1, hours=1, minutes=0
    )


def test_naive_datetimes_are_refused() -> None:
    with pytest.raises(ValueError, match="aware"):
        countdown_state(TARGET.replace(tzinfo=None), TARGET)
    with pytest.raises(ValueError, match="aware"):
        countdown_state(TARGET, TARGET.replace(tzinfo=None))


# --- The page cache never keeps an active countdown past its target


def test_without_an_active_countdown_the_max_age_is_unchanged() -> None:
    assert capped_max_age([], TARGET, 600) == 600
    assert capped_max_age([], TARGET, None) is None
    assert capped_max_age([TARGET - dt.timedelta(minutes=1)], TARGET, 600) == 600
    assert capped_max_age([TARGET], TARGET, 600) == 600


def test_the_max_age_ends_at_the_nearest_target() -> None:
    now = TARGET - dt.timedelta(seconds=90)
    later = TARGET + dt.timedelta(hours=1)
    assert capped_max_age([later, TARGET], now, 600) == 90
    assert capped_max_age([TARGET], now, 30) == 30


def test_a_lifetime_is_only_ever_shortened_never_added() -> None:
    # No max-age means the response was not cacheable for that long: capping must not make it so.
    now = TARGET - dt.timedelta(days=30)
    assert capped_max_age([TARGET], now, None) is None


def test_the_max_age_rounds_down_to_whole_seconds() -> None:
    now = TARGET - dt.timedelta(seconds=1, milliseconds=500)
    assert capped_max_age([TARGET], now, 600) == 1
    assert capped_max_age([TARGET], TARGET - dt.timedelta(milliseconds=10), 600) == 0
