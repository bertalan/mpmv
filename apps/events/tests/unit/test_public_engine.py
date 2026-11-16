"""EVT.6: the pure rules of the public pages: occurrences in time, the month grid, robots and
the single-event ICS (RFC 5545 escaping, folding and CRLF)."""

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from apps.events import engine

ROME = ZoneInfo("Europe/Rome")
NOW = dt.datetime(2026, 11, 16, 12, 0, tzinfo=dt.UTC)


def _timed(start: dt.datetime, hours: int = 8, **extra: object) -> engine.RecurrenceSpec:
    return engine.RecurrenceSpec(
        all_day=False,
        timezone="Europe/Rome",
        starts_at=start,
        ends_at=start + dt.timedelta(hours=hours),
        **extra,  # type: ignore[arg-type]
    )


def _all_day(first: dt.date, last: dt.date) -> engine.RecurrenceSpec:
    return engine.RecurrenceSpec(
        all_day=True, timezone="Europe/Rome", start_date=first, end_date=last
    )


def test_an_all_day_span_runs_midnight_to_midnight_and_lists_its_days() -> None:
    span = engine.first_span(_all_day(dt.date(2027, 8, 28), dt.date(2027, 8, 30)))
    assert span.start == dt.datetime(2027, 8, 28, tzinfo=ROME)
    assert span.end == dt.datetime(2027, 8, 31, tzinfo=ROME)
    assert span.days() == [dt.date(2027, 8, 28), dt.date(2027, 8, 29), dt.date(2027, 8, 30)]


def test_a_timed_span_ending_at_midnight_does_not_touch_the_next_day() -> None:
    start = dt.datetime(2027, 1, 5, 21, 0, tzinfo=ROME)
    span = engine.first_span(_timed(start, hours=3))
    assert span.days() == [dt.date(2027, 1, 5)]


def test_a_one_off_event_is_its_own_span_however_old() -> None:
    old = _timed(dt.datetime(2020, 5, 1, 8, 0, tzinfo=dt.UTC))
    assert engine.event_spans(old, NOW - dt.timedelta(days=30), NOW) == []
    assert len(engine.event_spans(old, dt.datetime(2020, 4, 1, tzinfo=dt.UTC), NOW)) == 1


def test_a_recurring_event_expands_inside_the_window() -> None:
    spec = _timed(dt.datetime(2026, 11, 1, 8, 0, tzinfo=dt.UTC), rrule="FREQ=WEEKLY;COUNT=6")
    spans = engine.event_spans(spec, NOW, NOW + dt.timedelta(days=15))
    assert [s.start.date() for s in spans] == [dt.date(2026, 11, 22), dt.date(2026, 11, 29)]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2027-03", (2027, 3)),
        ("2027-13", None),
        ("2027-3", None),
        ("abcd-ef", None),
        ("1800-01", None),
    ],
)
def test_parse_month(text: str, expected: tuple[int, int] | None) -> None:
    assert engine.parse_month(text) == expected


def test_the_month_grid_is_whole_weeks_starting_on_monday() -> None:
    weeks = engine.month_grid(2027, 3)
    assert all(len(week) == 7 for week in weeks)
    assert weeks[0][0].weekday() == 0
    assert weeks[0][0] <= dt.date(2027, 3, 1)
    assert weeks[-1][-1] >= dt.date(2027, 3, 31)
    assert engine.neighbour_month(2027, 12, 1) == (2028, 1)
    assert engine.neighbour_month(2027, 1, -1) == (2026, 12)


def test_month_bounds_follow_the_zone() -> None:
    start, end = engine.month_bounds(2027, 12, ROME)
    assert (start.date(), end.date()) == (dt.date(2027, 12, 1), dt.date(2028, 1, 1))
    assert start.tzinfo == ROME


def test_noindex_for_cancelled_and_for_events_over_30_days_past() -> None:
    soon = _timed(NOW + dt.timedelta(days=10))
    assert not engine.is_noindex(soon, cancelled=False, now=NOW)
    assert engine.is_noindex(soon, cancelled=True, now=NOW)
    ended_29 = _timed(NOW - dt.timedelta(days=29, hours=8))
    ended_31 = _timed(NOW - dt.timedelta(days=31, hours=8))
    assert not engine.is_noindex(ended_29, cancelled=False, now=NOW)
    assert engine.is_noindex(ended_31, cancelled=False, now=NOW)


def test_a_recurring_event_with_a_future_occurrence_is_indexable() -> None:
    spec = _timed(NOW - dt.timedelta(days=400), rrule="FREQ=YEARLY")
    assert not engine.is_noindex(spec, cancelled=False, now=NOW)


def test_ics_escape_handles_the_special_characters() -> None:
    assert engine.ics_escape("a,b;c\\d\ne") == "a\\,b\\;c\\\\d\\ne"


def test_ics_fold_keeps_lines_within_75_octets_and_utf8_whole() -> None:
    text = "SUMMARY:" + "è" * 100
    folded = engine.ics_fold(text)
    lines = folded.split("\r\n")
    assert lines[-1] == ""
    assert all(len(line.encode()) <= 75 for line in lines)
    assert all(line.startswith(" ") for line in lines[1:-1])
    assert "".join(line[1:] if i else line for i, line in enumerate(lines[:-1])) == text


def _ics(**changes: object) -> str:
    base = {
        "uid": "abc@example.test",
        "stamp": NOW,
        "created": NOW - dt.timedelta(days=3),
        "sequence": 2,
        "summary": "Fair, with; specials",
        "description": "Line one\nLine two",
        "location": "Hall 1, Milano",
        "url": "https://example.test/en/events/fair/",
        "category": "Trade fair",
        "span": engine.first_span(_timed(dt.datetime(2027, 3, 10, 8, 0, tzinfo=dt.UTC))),
        "cancelled": False,
        "calendar_name": "Events",
    }
    base.update(changes)
    return engine.event_ics(engine.IcsEvent(**base))  # type: ignore[arg-type]


def test_the_ics_body_is_crlf_folded_and_escaped() -> None:
    body = _ics()
    assert body.startswith("BEGIN:VCALENDAR\r\n")
    assert body.endswith("END:VCALENDAR\r\n")
    assert "\n" not in body.replace("\r\n", "")
    assert "SUMMARY:Fair\\, with\\; specials\r\n" in body
    assert "DESCRIPTION:Line one\\nLine two\r\n" in body
    assert "UID:abc@example.test\r\n" in body
    assert "SEQUENCE:2\r\n" in body
    assert "DTSTART:20270310T080000Z\r\nDTEND:20270310T160000Z\r\n" in body
    assert "STATUS:CONFIRMED\r\n" in body
    assert "TRANSP:TRANSPARENT\r\n" in body
    assert "ORGANIZER" not in body
    assert "ATTENDEE" not in body
    assert "GEO:" not in body  # no coordinates


def test_a_cancelled_event_and_coordinates_and_all_day_dates() -> None:
    body = _ics(
        cancelled=True,
        latitude="45.464204",
        longitude="9.189982",
        span=engine.first_span(_all_day(dt.date(2027, 8, 28), dt.date(2027, 8, 30))),
    )
    assert "STATUS:CANCELLED\r\n" in body
    assert "GEO:45.464204;9.189982\r\n" in body
    assert "DTSTART;VALUE=DATE:20270828\r\nDTEND;VALUE=DATE:20270831\r\n" in body  # exclusive end


def test_a_recurring_timed_event_keeps_its_zone_and_rule() -> None:
    span = engine.first_span(_timed(dt.datetime(2027, 3, 10, 8, 0, tzinfo=dt.UTC)))
    body = _ics(span=span, rrule="FREQ=YEARLY;COUNT=3")
    assert "DTSTART;TZID=Europe/Rome:20270310T090000\r\n" in body
    assert "RRULE:FREQ=YEARLY;COUNT=3\r\n" in body
