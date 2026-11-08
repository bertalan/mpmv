"""EVT.2: the RRULE subset validator and the expansion of occurrences (spec 15, cases 5 to 8)."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from apps.events import engine
from apps.events.engine import Occurrence, RecurrenceError, RecurrenceSpec

ROME = ZoneInfo("Europe/Rome")


def utc(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=UTC)  # type: ignore[misc]


def timed(
    starts_at: datetime,
    rrule: str,
    *,
    minutes: int = 60,
    exdates: tuple[datetime, ...] = (),
    zone: str = "Europe/Rome",
) -> RecurrenceSpec:
    return RecurrenceSpec(
        all_day=False,
        timezone=zone,
        rrule=rrule,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=minutes),
        exdates=exdates,
    )


def all_day(
    start: date, end: date, rrule: str, *, exdate_days: tuple[date, ...] = ()
) -> RecurrenceSpec:
    return RecurrenceSpec(
        all_day=True,
        timezone="Europe/Rome",
        rrule=rrule,
        start_date=start,
        end_date=end,
        exdate_days=exdate_days,
    )


def starts_utc(occurrences: list[Occurrence]) -> list[datetime]:
    return [o.start.astimezone(UTC) for o in occurrences]  # type: ignore[union-attr]


# Regression case 5: a weekly 10:00 meeting stays at 10:00 local across DST changes.


def test_case_5_weekly_event_keeps_local_time_when_dst_ends() -> None:
    spec = timed(utc(2026, 10, 19, 8), "FREQ=WEEKLY")
    occurrences = engine.expand(spec, utc(2026, 10, 1), utc(2026, 11, 1))
    assert starts_utc(occurrences) == [utc(2026, 10, 19, 8), utc(2026, 10, 26, 9)]
    for occurrence in occurrences:
        assert isinstance(occurrence.start, datetime)
        assert occurrence.start.tzinfo == ROME
        assert (occurrence.start.hour, occurrence.start.minute) == (10, 0)
        assert occurrence.end - occurrence.start == timedelta(hours=1)  # type: ignore[operator]


def test_case_5_weekly_event_goes_back_to_0800_utc_when_dst_starts() -> None:
    spec = timed(utc(2026, 10, 19, 8), "FREQ=WEEKLY")
    occurrences = engine.expand(spec, utc(2027, 3, 20), utc(2027, 4, 1))
    assert starts_utc(occurrences) == [utc(2027, 3, 22, 9), utc(2027, 3, 29, 8)]


# Regression case 6: non-existent and ambiguous local times.


def test_case_6_non_existent_local_time_moves_forward_by_the_gap() -> None:
    spec = timed(utc(2027, 3, 26, 1, 30), "FREQ=DAILY")  # 02:30 CET
    occurrences = engine.expand(spec, utc(2027, 3, 26), utc(2027, 3, 30))
    local = [o.start.astimezone(ROME) for o in occurrences]  # type: ignore[union-attr]
    assert [(t.day, t.hour, t.minute) for t in local] == [
        (26, 2, 30),
        (27, 2, 30),
        (28, 3, 30),  # 02:30 does not exist on 28 March 2027
        (29, 2, 30),
    ]
    assert starts_utc(occurrences)[2] == utc(2027, 3, 28, 1, 30)


def test_case_6_ambiguous_local_time_takes_the_first_instance() -> None:
    spec = timed(utc(2026, 10, 23, 0, 30), "FREQ=DAILY")  # 02:30 CEST
    occurrences = engine.expand(spec, utc(2026, 10, 23), utc(2026, 10, 27))
    assert starts_utc(occurrences) == [
        utc(2026, 10, 23, 0, 30),
        utc(2026, 10, 24, 0, 30),
        utc(2026, 10, 25, 0, 30),  # 02:30 occurs twice: the first one, still CEST
        utc(2026, 10, 26, 1, 30),
    ]


# Regression case 7: "a fair every year from 28 August to 2 September".


def _fair(rrule: str, exdate_days: tuple[date, ...] = ()) -> RecurrenceSpec:
    return all_day(date(2026, 8, 28), date(2026, 9, 2), rrule, exdate_days=exdate_days)


def _spans(occurrences: list[Occurrence]) -> list[tuple[object, object]]:
    return [(o.start, o.end) for o in occurrences]


def test_case_7_yearly_fair_keeps_its_six_days() -> None:
    occurrences = engine.expand(_fair("FREQ=YEARLY"), utc(2026, 1, 1), utc(2028, 12, 31))
    assert _spans(occurrences) == [
        (date(2026, 8, 28), date(2026, 9, 2)),
        (date(2027, 8, 28), date(2027, 9, 2)),
        (date(2028, 8, 28), date(2028, 9, 2)),  # a leap year changes nothing
    ]


def test_case_7_until_stops_the_fair() -> None:
    occurrences = engine.expand(
        _fair("FREQ=YEARLY;UNTIL=20270828"), utc(2026, 1, 1), utc(2030, 1, 1)
    )
    assert [o.start for o in occurrences] == [date(2026, 8, 28), date(2027, 8, 28)]


def test_case_7_count_stops_the_fair() -> None:
    occurrences = engine.expand(_fair("FREQ=YEARLY;COUNT=2"), utc(2026, 1, 1), utc(2030, 1, 1))
    assert [o.start for o in occurrences] == [date(2026, 8, 28), date(2027, 8, 28)]


def test_case_7_an_exdate_removes_one_year() -> None:
    spec = _fair("FREQ=YEARLY;COUNT=3", exdate_days=(date(2027, 8, 28),))
    occurrences = engine.expand(spec, utc(2026, 1, 1), utc(2030, 1, 1))
    assert [o.start for o in occurrences] == [date(2026, 8, 28), date(2028, 8, 28)]


def test_an_ongoing_fair_overlaps_the_window() -> None:
    occurrences = engine.expand(_fair("FREQ=YEARLY"), utc(2027, 8, 31), utc(2027, 9, 1))
    assert [o.start for o in occurrences] == [date(2027, 8, 28)]


def test_a_timed_exdate_removes_one_occurrence() -> None:
    spec = timed(utc(2026, 10, 19, 8), "FREQ=WEEKLY", exdates=(utc(2026, 10, 26, 9),))
    occurrences = engine.expand(spec, utc(2026, 10, 1), utc(2026, 11, 5))
    assert starts_utc(occurrences) == [utc(2026, 10, 19, 8), utc(2026, 11, 2, 9)]


# Regression case 8: refused parts name the part.


@pytest.mark.parametrize(
    ("rrule", "part"),
    [
        ("FREQ=MONTHLY;BYDAY=MO;BYSETPOS=1", "BYSETPOS"),
        ("FREQ=HOURLY", "FREQ"),
        ("FREQ=MINUTELY", "FREQ"),
        ("FREQ=SECONDLY", "FREQ"),
        ("FREQ=DAILY;BYHOUR=10", "BYHOUR"),
        ("FREQ=DAILY;BYMINUTE=10", "BYMINUTE"),
        ("FREQ=DAILY;BYSECOND=10", "BYSECOND"),
        ("FREQ=YEARLY;BYYEARDAY=100", "BYYEARDAY"),
        ("FREQ=YEARLY;BYWEEKNO=20", "BYWEEKNO"),
        ("FREQ=DAILY;RDATE=20270101", "RDATE"),
        ("FREQ=WEEKLY;COUNT=3;UNTIL=20271231T000000Z", "COUNT"),
        ("FREQ=WEEKLY;INTERVAL=13", "INTERVAL"),
        ("FREQ=WEEKLY;INTERVAL=0", "INTERVAL"),
        ("FREQ=WEEKLY;BYDAY=1MO", "BYDAY"),
        ("FREQ=MONTHLY;BYDAY=XX", "BYDAY"),
        ("FREQ=MONTHLY;BYMONTHDAY=-2", "BYMONTHDAY"),
        ("FREQ=MONTHLY;BYMONTHDAY=32", "BYMONTHDAY"),
        ("FREQ=YEARLY;BYMONTH=13", "BYMONTH"),
        ("FREQ=WEEKLY;COUNT=0", "COUNT"),
        ("FREQ=WEEKLY;UNTIL=20271231", "UNTIL"),  # timed events need a UTC timestamp
        ("FREQ=WEEKLY;WKST=XX", "WKST"),
        ("FREQ=WEEKLY;FREQ=DAILY", "FREQ"),
        ("BYDAY=MO", "FREQ"),
        ("FREQ=WEEKLY;BOGUS=1", "BOGUS"),
        ("FREQ=WEEKLY;BYDAY", "BYDAY"),
    ],
)
def test_case_8_refused_parts_are_named(rrule: str, part: str) -> None:
    with pytest.raises(RecurrenceError) as raised:
        engine.validate_rrule(rrule, all_day=False)
    assert part in [error.part for error in raised.value.errors]
    assert all(error.message for error in raised.value.errors)


def test_all_day_until_must_be_a_date() -> None:
    with pytest.raises(RecurrenceError) as raised:
        engine.validate_rrule("FREQ=YEARLY;UNTIL=20271231T000000Z", all_day=True)
    assert [error.part for error in raised.value.errors] == ["UNTIL"]


def test_every_refused_part_is_reported_at_once() -> None:
    with pytest.raises(RecurrenceError) as raised:
        engine.validate_rrule("FREQ=HOURLY;BYSETPOS=1;RDATE=20270101", all_day=False)
    assert {e.part for e in raised.value.errors} == {"FREQ", "BYSETPOS", "RDATE"}


def test_expand_refuses_an_invalid_rule() -> None:
    with pytest.raises(RecurrenceError):
        engine.expand(timed(utc(2026, 10, 19, 8), "FREQ=HOURLY"), utc(2026, 1, 1), utc(2027, 1, 1))


@pytest.mark.parametrize(
    ("rrule", "all_day_flag"),
    [
        ("FREQ=WEEKLY;BYDAY=MO,WE;WKST=SU", False),
        ("RRULE:FREQ=MONTHLY;BYDAY=1SA", False),
        ("freq=monthly;byday=-1fr;count=4", False),
        ("FREQ=MONTHLY;BYMONTHDAY=-1;INTERVAL=12", False),
        ("FREQ=YEARLY;BYMONTH=8;BYDAY=-1SA;UNTIL=20301231T230000Z", False),
        ("FREQ=YEARLY;UNTIL=20300901", True),
        ("FREQ=DAILY;COUNT=5", True),
    ],
)
def test_the_subset_is_accepted(rrule: str, all_day_flag: bool) -> None:
    subset = engine.validate_rrule(rrule, all_day=all_day_flag)
    assert subset.freq in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY")


def test_the_parsed_subset() -> None:
    subset = engine.validate_rrule("FREQ=MONTHLY;INTERVAL=2;BYDAY=1SA,-1FR;COUNT=4", False)
    assert subset.freq == "MONTHLY"
    assert subset.interval == 2
    assert subset.count == 4
    assert subset.until is None
    assert subset.byday == ((1, 5), (-1, 4))
    timed_until = engine.validate_rrule("FREQ=WEEKLY;UNTIL=20271231T120000Z", False).until
    assert timed_until == utc(2027, 12, 31, 12)
    assert engine.validate_rrule("FREQ=YEARLY;UNTIL=20271231", True).until == date(2027, 12, 31)


# The supported patterns: "every Monday", "first Saturday of the month", "every August".


def test_first_saturday_of_the_month() -> None:
    spec = timed(utc(2026, 11, 7, 9), "FREQ=MONTHLY;BYDAY=1SA")
    occurrences = engine.expand(spec, utc(2026, 11, 1), utc(2027, 2, 1))
    assert [o.start.date() for o in occurrences] == [  # type: ignore[union-attr]
        date(2026, 11, 7),
        date(2026, 12, 5),
        date(2027, 1, 2),
    ]


def test_last_friday_of_the_month() -> None:
    spec = timed(utc(2026, 11, 27, 17), "FREQ=MONTHLY;BYDAY=-1FR;COUNT=3")
    occurrences = engine.expand(spec, utc(2026, 1, 1), utc(2028, 1, 1))
    assert [o.start.date() for o in occurrences] == [  # type: ignore[union-attr]
        date(2026, 11, 27),
        date(2026, 12, 25),
        date(2027, 1, 29),
    ]


def test_every_monday_and_wednesday() -> None:
    spec = timed(utc(2026, 11, 9, 9), "FREQ=WEEKLY;BYDAY=MO,WE")
    occurrences = engine.expand(spec, utc(2026, 11, 1), utc(2026, 11, 20))
    assert [o.start.day for o in occurrences] == [9, 11, 16, 18]  # type: ignore[union-attr]


def test_every_august_on_the_last_saturday() -> None:
    spec = timed(utc(2026, 8, 29, 8), "FREQ=YEARLY;BYMONTH=8;BYDAY=-1SA")
    occurrences = engine.expand(spec, utc(2026, 1, 1), utc(2029, 1, 1))
    assert [o.start.date() for o in occurrences] == [  # type: ignore[union-attr]
        date(2026, 8, 29),
        date(2027, 8, 28),
        date(2028, 8, 26),
    ]


def test_last_day_of_the_month() -> None:
    spec = timed(utc(2027, 1, 31, 9), "FREQ=MONTHLY;BYMONTHDAY=-1;COUNT=3")
    occurrences = engine.expand(spec, utc(2027, 1, 1), utc(2028, 1, 1))
    assert [o.start.date() for o in occurrences] == [  # type: ignore[union-attr]
        date(2027, 1, 31),
        date(2027, 2, 28),
        date(2027, 3, 31),
    ]


def test_monthly_on_the_31st_skips_short_months() -> None:
    spec = timed(utc(2027, 1, 31, 9), "FREQ=MONTHLY;COUNT=3")
    occurrences = engine.expand(spec, utc(2027, 1, 1), utc(2028, 1, 1))
    assert [o.start.month for o in occurrences] == [1, 3, 5]  # type: ignore[union-attr]


def test_interval_skips_periods() -> None:
    spec = timed(utc(2026, 11, 9, 9), "FREQ=WEEKLY;INTERVAL=2")
    occurrences = engine.expand(spec, utc(2026, 11, 1), utc(2026, 12, 10))
    assert [o.start.day for o in occurrences] == [9, 23, 7]  # type: ignore[union-attr]


def test_a_single_event_without_a_rule() -> None:
    spec = timed(utc(2026, 11, 9, 9), "")
    assert starts_utc(engine.expand(spec, utc(2026, 11, 1), utc(2026, 12, 1))) == [
        utc(2026, 11, 9, 9)
    ]
    assert engine.expand(spec, utc(2026, 12, 1), utc(2027, 1, 1)) == []


# Horizon, limit and window.


def test_limit_caps_runaway_rules() -> None:
    spec = timed(utc(2026, 11, 9, 9), "FREQ=DAILY")
    assert len(engine.expand(spec, utc(2026, 11, 1), utc(2027, 11, 1), limit=10)) == 10
    assert len(engine.expand(spec, utc(2026, 11, 1), utc(2028, 11, 1))) == engine.DEFAULT_LIMIT


def test_the_window_is_capped_at_the_horizon() -> None:
    spec = timed(utc(2026, 11, 9, 9), "FREQ=MONTHLY")
    occurrences = engine.expand(spec, utc(2026, 11, 1), utc(2036, 11, 1))
    assert len(occurrences) == engine.HORIZON_MONTHS
    assert occurrences[-1].start < engine.horizon_end(utc(2026, 11, 1))  # type: ignore[operator]
    assert engine.horizon_end(utc(2026, 11, 1)) == utc(2028, 5, 1)


def test_naive_window_and_bad_limit_are_refused() -> None:
    spec = timed(utc(2026, 11, 9, 9), "FREQ=DAILY")
    with pytest.raises(ValueError, match="aware"):
        engine.expand(spec, datetime(2026, 11, 1), utc(2026, 12, 1))
    with pytest.raises(ValueError, match="limit"):
        engine.expand(spec, utc(2026, 11, 1), utc(2026, 12, 1), limit=0)


# Properties over a spread of rules and windows.

RULES = [
    "FREQ=DAILY",
    "FREQ=DAILY;INTERVAL=3;BYDAY=MO,FR",
    "FREQ=WEEKLY;BYDAY=MO,TH,SU;WKST=SU",
    "FREQ=WEEKLY;INTERVAL=2;COUNT=20",
    "FREQ=MONTHLY;BYDAY=1SA,-1SU",
    "FREQ=MONTHLY;BYMONTHDAY=1,15,-1",
    "FREQ=MONTHLY;BYMONTHDAY=13;BYDAY=FR",
    "FREQ=YEARLY;BYMONTH=3,10;BYDAY=-1SU",
    "FREQ=YEARLY;BYMONTH=8",
    "FREQ=YEARLY;BYDAY=20MO",
    "FREQ=YEARLY;BYMONTHDAY=29;BYMONTH=2",
]
WINDOWS = [
    (utc(2026, 9, 1), utc(2027, 6, 1)),
    (utc(2027, 3, 27), utc(2027, 3, 29)),
    (utc(2028, 1, 1), utc(2030, 1, 1)),
]


@pytest.mark.parametrize("rrule", RULES)
@pytest.mark.parametrize(("window_start", "window_end"), WINDOWS)
@pytest.mark.parametrize("limit", [3, 500])
def test_expansion_properties(
    rrule: str, window_start: datetime, window_end: datetime, limit: int
) -> None:
    spec = timed(utc(2026, 10, 25, 1, 30), rrule, minutes=90)  # 02:30 CET, ambiguous day
    occurrences = engine.expand(spec, window_start, window_end, limit=limit)
    starts = starts_utc(occurrences)
    assert starts == sorted(starts)
    assert len(set(starts)) == len(starts)
    assert len(occurrences) <= limit
    for occurrence in occurrences:
        assert isinstance(occurrence.start, datetime)
        assert isinstance(occurrence.end, datetime)
        assert occurrence.start.tzinfo == ROME
        assert occurrence.start < window_end
        assert occurrence.end > window_start
        assert occurrence.end - occurrence.start == timedelta(minutes=90)
        assert occurrence.start >= spec.starts_at  # type: ignore[operator]


@pytest.mark.parametrize("rrule", ["FREQ=DAILY", "FREQ=WEEKLY;BYDAY=SA", "FREQ=MONTHLY"])
def test_all_day_properties(rrule: str) -> None:
    spec = all_day(date(2026, 10, 24), date(2026, 10, 25), rrule)
    occurrences = engine.expand(spec, utc(2026, 10, 1), utc(2027, 4, 1), limit=50)
    starts = [o.start for o in occurrences]
    assert starts == sorted(starts)
    assert len(set(starts)) == len(starts)
    for occurrence in occurrences:
        assert type(occurrence.start) is date
        assert occurrence.end - occurrence.start == timedelta(days=1)  # type: ignore[operator]
