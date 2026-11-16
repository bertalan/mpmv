"""EVT.4: the vendor form's recurrence presets write and read an RRULE (spec 15 "Recurrence").

The presets are pure functions of `apps.events.engine`: every rule they write passes
`validate_rrule`, and the form shows a stored rule again as the same preset.
"""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from apps.events import engine
from apps.events.engine import RecurrenceError, RecurrenceSpec


def test_no_preset_writes_no_rule() -> None:
    assert engine.build_rrule(engine.PRESET_NONE) == ""


def test_yearly_repeats_on_the_events_own_dates() -> None:
    assert engine.build_rrule(engine.PRESET_YEARLY) == "FREQ=YEARLY"


def test_weekly_lists_the_chosen_weekdays_in_week_order() -> None:
    rule = engine.build_rrule(engine.PRESET_WEEKLY, weekdays=("WE", "MO"))
    assert rule == "FREQ=WEEKLY;BYDAY=MO,WE"


def test_weekly_needs_a_weekday_and_refuses_an_unknown_one() -> None:
    with pytest.raises(RecurrenceError) as none_chosen:
        engine.build_rrule(engine.PRESET_WEEKLY)
    assert [e.part for e in none_chosen.value.errors] == ["BYDAY"]
    with pytest.raises(RecurrenceError):
        engine.build_rrule(engine.PRESET_WEEKLY, weekdays=("XX",))


def test_monthly_takes_a_day_of_the_month_or_the_last_day() -> None:
    assert engine.build_rrule(engine.PRESET_MONTHLY, month_day=15) == "FREQ=MONTHLY;BYMONTHDAY=15"
    assert engine.build_rrule(engine.PRESET_MONTHLY, month_day=-1) == "FREQ=MONTHLY;BYMONTHDAY=-1"
    for bad in (None, 0, 32):
        with pytest.raises(RecurrenceError):
            engine.build_rrule(engine.PRESET_MONTHLY, month_day=bad)


def test_the_interval_is_between_one_and_twelve() -> None:
    rule = engine.build_rrule(engine.PRESET_WEEKLY, weekdays=("MO",), interval=2)
    assert rule == "FREQ=WEEKLY;INTERVAL=2;BYDAY=MO"
    with pytest.raises(RecurrenceError):
        engine.build_rrule(engine.PRESET_YEARLY, interval=13)


def test_an_unknown_preset_is_refused() -> None:
    with pytest.raises(RecurrenceError):
        engine.build_rrule("hourly")


def test_the_end_is_never_a_count_or_a_last_day() -> None:
    counted = engine.build_rrule(engine.PRESET_YEARLY, ends=engine.ENDS_COUNT, count=3)
    assert counted == "FREQ=YEARLY;COUNT=3"
    with pytest.raises(RecurrenceError):
        engine.build_rrule(engine.PRESET_YEARLY, ends=engine.ENDS_COUNT)
    with pytest.raises(RecurrenceError):
        engine.build_rrule(engine.PRESET_YEARLY, ends=engine.ENDS_UNTIL)


def test_an_all_day_event_ends_on_a_date() -> None:
    rule = engine.build_rrule(
        engine.PRESET_YEARLY, ends=engine.ENDS_UNTIL, until=date(2029, 12, 31), all_day=True
    )
    assert rule == "FREQ=YEARLY;UNTIL=20291231"


@pytest.mark.parametrize(
    ("day", "stamp"),
    [
        (date(2027, 12, 31), "20271231T225959Z"),  # winter: CET is UTC+1
        (date(2027, 8, 31), "20270831T215959Z"),  # summer: CEST is UTC+2
    ],
)
def test_a_timed_event_ends_at_the_end_of_that_day_in_its_zone_in_utc(
    day: date, stamp: str
) -> None:
    rule = engine.build_rrule(
        engine.PRESET_YEARLY,
        ends=engine.ENDS_UNTIL,
        until=day,
        timezone="Europe/Rome",
    )
    assert rule == f"FREQ=YEARLY;UNTIL={stamp}"


def test_every_rule_a_preset_writes_passes_the_validator() -> None:
    rules = [
        engine.build_rrule(engine.PRESET_YEARLY, interval=2, ends=engine.ENDS_COUNT, count=4),
        engine.build_rrule(engine.PRESET_WEEKLY, weekdays=("SA", "SU")),
        engine.build_rrule(engine.PRESET_MONTHLY, month_day=1, interval=3),
    ]
    for rule in rules:
        assert engine.validate_rrule(rule, all_day=False).freq


def test_a_weekly_preset_expands_to_the_chosen_days() -> None:
    zone = ZoneInfo("Europe/Rome")
    rule = engine.build_rrule(engine.PRESET_WEEKLY, weekdays=("MO", "WE"))
    start = datetime(2027, 3, 1, 10, 0, tzinfo=zone)  # a Monday
    spec = RecurrenceSpec(
        all_day=False,
        timezone="Europe/Rome",
        rrule=rule,
        starts_at=start,
        ends_at=start.replace(hour=11),
    )
    found = engine.expand(spec, datetime(2027, 3, 1, tzinfo=UTC), datetime(2027, 3, 15, tzinfo=UTC))
    assert [o.start.day for o in found] == [1, 3, 8, 10]  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "rule",
    [
        "FREQ=YEARLY",
        "FREQ=YEARLY;INTERVAL=2;COUNT=4",
        "FREQ=WEEKLY;BYDAY=MO,WE",
        "FREQ=WEEKLY;INTERVAL=2;BYDAY=SA",
        "FREQ=MONTHLY;BYMONTHDAY=15",
        "FREQ=MONTHLY;BYMONTHDAY=-1;COUNT=6",
    ],
)
def test_a_stored_preset_rule_reads_back_as_the_same_choice(rule: str) -> None:
    choice = engine.preset_from_rrule(rule, timezone="Europe/Rome")
    assert choice is not None
    again = engine.build_rrule(
        choice.preset,
        weekdays=choice.weekdays,
        month_day=choice.month_day,
        interval=choice.interval,
        ends=choice.ends,
        count=choice.count,
        until=choice.until,
        timezone="Europe/Rome",
    )
    assert again == rule


def test_an_until_reads_back_as_the_local_last_day() -> None:
    choice = engine.preset_from_rrule("FREQ=YEARLY;UNTIL=20271231T225959Z", timezone="Europe/Rome")
    assert choice is not None
    assert (choice.ends, choice.until) == (engine.ENDS_UNTIL, date(2027, 12, 31))
    all_day = engine.preset_from_rrule("FREQ=YEARLY;UNTIL=20291231", all_day=True)
    assert all_day is not None
    assert all_day.until == date(2029, 12, 31)


def test_a_rule_the_presets_cannot_write_is_custom_and_an_empty_rule_is_none() -> None:
    assert engine.preset_from_rrule("") == engine.PresetChoice(engine.PRESET_NONE)
    assert engine.preset_from_rrule("FREQ=MONTHLY;BYDAY=1SA") is None
    assert engine.preset_from_rrule("FREQ=DAILY") is None
    assert engine.preset_from_rrule("not a rule") is None
