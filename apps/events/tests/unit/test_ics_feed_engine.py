"""EVT.9: the pure ICS serializer for feeds (spec 15 "Feeds and calendars"): several events in
one calendar, TZID plus one VTIMEZONE per zone used, `LANGUAGE` on fallback items, the calendar
properties, and everything parsing with `icalendar`."""

import datetime as dt

from icalendar import Calendar

from apps.events import engine

NOW = dt.datetime(2027, 1, 5, 12, 0, tzinfo=dt.UTC)


def _span(zone: str = "Europe/Rome", *, all_day: bool = False) -> engine.Span:
    if all_day:
        spec = engine.RecurrenceSpec(
            all_day=True,
            timezone=zone,
            start_date=dt.date(2027, 8, 28),
            end_date=dt.date(2027, 8, 30),
        )
    else:
        spec = engine.RecurrenceSpec(
            all_day=False,
            timezone=zone,
            starts_at=dt.datetime(2027, 3, 10, 8, 0, tzinfo=dt.UTC),
            ends_at=dt.datetime(2027, 3, 10, 16, 0, tzinfo=dt.UTC),
        )
    return engine.first_span(spec)


def _event(uid: str = "a@example.test", **changes: object) -> engine.IcsEvent:
    base: dict[str, object] = {
        "uid": uid,
        "stamp": NOW,
        "created": None,
        "sequence": 0,
        "summary": "Fair",
        "description": "About the fair",
        "location": "Hall 1",
        "url": "https://example.test/en/events/fair/",
        "category": "Trade fair",
        "span": _span(),
        "cancelled": False,
        "calendar_name": "Events",
    }
    base.update(changes)
    return engine.IcsEvent(**base)  # type: ignore[arg-type]


def test_a_calendar_of_several_events_parses_with_icalendar() -> None:
    body = engine.calendar_ics([_event("a@x"), _event("b@x", summary="Other")], "Events")
    calendar = Calendar.from_ical(body)
    events = calendar.walk("VEVENT")
    assert [str(event["UID"]) for event in events] == ["a@x", "b@x"]
    assert str(calendar["VERSION"]) == "2.0"
    assert str(calendar["METHOD"]) == "PUBLISH"
    assert str(calendar["CALSCALE"]) == "GREGORIAN"
    assert str(calendar["X-WR-CALNAME"]) == "Events"
    assert str(calendar["NAME"]) == "Events"
    assert "REFRESH-INTERVAL;VALUE=DURATION:PT6H\r\n" in body
    assert "X-PUBLISHED-TTL:PT6H\r\n" in body
    assert body.endswith("END:VCALENDAR\r\n")


def test_an_empty_calendar_is_still_a_valid_calendar() -> None:
    calendar = Calendar.from_ical(engine.calendar_ics([], "Events"))
    assert calendar.walk("VEVENT") == []


def test_a_timed_event_has_tzid_and_one_vtimezone_per_zone_used() -> None:
    body = engine.calendar_ics(
        [_event("a@x"), _event("b@x"), _event("c@x", span=_span("Europe/Berlin"))], "Events"
    )
    calendar = Calendar.from_ical(body)
    assert sorted(str(zone["TZID"]) for zone in calendar.walk("VTIMEZONE")) == [
        "Europe/Berlin",
        "Europe/Rome",
    ]
    assert "DTSTART;TZID=Europe/Rome:20270310T090000\r\n" in body
    assert "DTEND;TZID=Europe/Rome:20270310T170000\r\n" in body


def test_all_day_events_need_no_vtimezone() -> None:
    body = engine.calendar_ics([_event(span=_span(all_day=True))], "Events")
    assert "VTIMEZONE" not in body
    assert "DTSTART;VALUE=DATE:20270828\r\n" in body


def test_the_body_is_stable_between_changes() -> None:
    events = [_event("a@x"), _event("b@x", span=_span("Europe/Berlin"))]
    assert engine.calendar_ics(events, "Events") == engine.calendar_ics(events, "Events")


def test_a_fallback_item_carries_the_language_on_text_properties() -> None:
    body = engine.calendar_ics([_event(language="de")], "Events")
    assert "SUMMARY;LANGUAGE=de:Fair\r\n" in body
    assert "DESCRIPTION;LANGUAGE=de:About the fair\r\n" in body
    event = Calendar.from_ical(body).walk("VEVENT")[0]
    assert event["SUMMARY"].params["LANGUAGE"] == "de"
    assert "LANGUAGE" not in engine.calendar_ics([_event()], "Events")


def test_rrule_and_exdate_pass_through_in_one_vevent() -> None:
    body = engine.calendar_ics(
        [
            _event(
                rrule="FREQ=WEEKLY;COUNT=10",
                exdates=(dt.datetime(2027, 3, 17, 8, 0, tzinfo=dt.UTC),),
            )
        ],
        "Events",
    )
    events = Calendar.from_ical(body).walk("VEVENT")
    assert len(events) == 1
    assert "FREQ=WEEKLY;COUNT=10" in str(events[0]["RRULE"].to_ical())
    assert "EXDATE;TZID=Europe/Rome:20270317T090000\r\n" in body  # same TZID form as DTSTART


def test_location_carries_the_language_of_a_fallback_item() -> None:
    body = engine.calendar_ics([_event(language="de")], "Events")
    assert Calendar.from_ical(body).walk("VEVENT")[0]["LOCATION"].params["LANGUAGE"] == "de"
    assert "LOCATION;LANGUAGE" not in engine.calendar_ics([_event()], "Events")


def test_an_unbounded_rule_ends_its_timezone_with_yearly_rules() -> None:
    unbounded = engine.calendar_ics([_event(rrule="FREQ=WEEKLY")], "Events")
    assert "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU\r\n" in unbounded
    assert "RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU\r\n" in unbounded
    Calendar.from_ical(unbounded)
    bounded = engine.calendar_ics([_event(rrule="FREQ=WEEKLY;COUNT=4")], "Events")
    assert "FREQ=YEARLY" not in bounded


def test_hostile_text_is_escaped_and_cannot_inject_a_line() -> None:
    nasty = "A, B; C \\ D\r\nEND:VEVENT\nBEGIN:X ]]> < & caf\u00e9 \u00e8"
    body = engine.calendar_ics([_event(summary=nasty, description=nasty, location=nasty)], "Events")
    calendar = Calendar.from_ical(body)
    assert len(calendar.walk("VEVENT")) == 1
    assert not calendar.walk("X")
    assert "caf\u00e9" in str(calendar.walk("VEVENT")[0]["SUMMARY"])
    assert "\r\nEND:VEVENT\r\nEND:VEVENT" not in body
