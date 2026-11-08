"""EVT.1 done-when: constraints tested (end after start, one of COUNT or UNTIL, no `closure`
type, regression case 19), both in `clean()` and in the database's check constraints.

Spec 15 regression case 19: latitude 90.000001 or longitude -180.5 is refused; a latitude
without a longitude is refused; a promotion or an online event with coordinates is refused;
six decimal places are kept exactly.
"""

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.events.models import EventPage
from apps.events.tests.factories import EventPageFactory

pytestmark = pytest.mark.django_db


def _refused_by_database(page: EventPage, **values: Any) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        EventPage.objects.filter(pk=page.pk).update(**values)


def _refused_by_clean(page: EventPage, field: str, **values: Any) -> None:
    for name, value in values.items():
        setattr(page, name, value)
    with pytest.raises(ValidationError) as error:
        page.full_clean()
    assert field in error.value.message_dict, error.value.message_dict


def test_a_valid_event_passes_both() -> None:
    page = EventPageFactory.create(located=True)
    page.full_clean()


# --- End after start


def test_a_timed_event_ends_after_it_starts() -> None:
    page = EventPageFactory.create()
    _refused_by_database(page, ends_at=page.starts_at)
    _refused_by_database(page, ends_at=page.starts_at - dt.timedelta(minutes=1))
    _refused_by_clean(page, "ends_at", ends_at=page.starts_at)


def test_an_all_day_event_ends_on_or_after_its_first_day() -> None:
    page = EventPageFactory.create(all_day=True)
    page.full_clean()
    EventPage.objects.filter(pk=page.pk).update(end_date=page.start_date)  # one day: allowed
    _refused_by_database(page, end_date=page.start_date - dt.timedelta(days=1))
    _refused_by_clean(page, "end_date", end_date=page.start_date - dt.timedelta(days=1))


def test_timed_events_use_timestamps_and_all_day_events_use_dates() -> None:
    timed = EventPageFactory.create()
    _refused_by_database(timed, starts_at=None)
    _refused_by_database(timed, start_date=dt.date(2027, 3, 10), end_date=dt.date(2027, 3, 10))
    all_day = EventPageFactory.create(all_day=True)
    _refused_by_database(all_day, end_date=None)
    _refused_by_database(all_day, starts_at=timed.starts_at, ends_at=timed.ends_at)
    _refused_by_clean(timed, "start_date", all_day=True)


def test_the_time_zone_is_an_iana_name() -> None:
    page = EventPageFactory.create()
    _refused_by_clean(page, "timezone", timezone="Mars/Olympus")


# --- One of COUNT or UNTIL


def test_an_rrule_with_both_count_and_until_is_refused() -> None:
    page = EventPageFactory.create(rrule="FREQ=YEARLY;COUNT=3")
    page.full_clean()
    both = "FREQ=YEARLY;COUNT=3;UNTIL=20301231T000000Z"
    _refused_by_database(page, rrule=both)
    _refused_by_clean(page, "rrule", rrule=both)


def test_exclusion_dates_follow_the_kind_of_event() -> None:
    timed = EventPageFactory.create()
    _refused_by_database(timed, exdate_days=[dt.date(2027, 3, 17)])
    all_day = EventPageFactory.create(all_day=True)
    _refused_by_database(all_day, exdates=[timed.starts_at])


# --- No closure type (owner decision Q-73)


def test_closure_is_not_an_event_type() -> None:
    assert "closure" not in EventPage.EventType.values
    page = EventPageFactory.create()
    _refused_by_database(page, event_type="closure")
    _refused_by_clean(page, "event_type", event_type="closure")


# --- Regression case 19: coordinates


def test_latitude_beyond_ninety_is_refused() -> None:
    page = EventPageFactory.create(located=True)
    _refused_by_database(page, latitude=Decimal("90.000001"))
    _refused_by_clean(page, "latitude", latitude=Decimal("90.000001"))


def test_longitude_beyond_minus_one_eighty_is_refused() -> None:
    page = EventPageFactory.create(located=True)
    _refused_by_database(page, longitude=Decimal("-180.5"))
    _refused_by_clean(page, "longitude", longitude=Decimal("-180.5"))


def test_the_extremes_are_allowed() -> None:
    page = EventPageFactory.create(located=True)
    EventPage.objects.filter(pk=page.pk).update(
        latitude=Decimal("-90.000000"), longitude=Decimal("180.000000")
    )


def test_a_latitude_without_a_longitude_is_refused() -> None:
    page = EventPageFactory.create(located=True)
    _refused_by_database(page, longitude=None)
    _refused_by_clean(page, "longitude", longitude=None)


def test_a_promotion_with_coordinates_is_refused() -> None:
    page = EventPageFactory.create(promotion=True)
    page.full_clean()
    coordinates = {"latitude": Decimal("45.464204"), "longitude": Decimal("9.189982")}
    _refused_by_database(page, **coordinates)
    _refused_by_clean(page, "latitude", **coordinates)


def test_a_promotion_has_no_attendance_mode_or_venue() -> None:
    page = EventPageFactory.create(promotion=True)
    _refused_by_database(page, attendance=EventPage.Attendance.IN_PERSON)
    _refused_by_database(page, venue_name="Hall 1")
    _refused_by_clean(page, "venue_name", venue_name="Hall 1")


def test_an_online_event_with_coordinates_is_refused() -> None:
    page = EventPageFactory.create(online=True)
    page.full_clean()
    coordinates = {"latitude": Decimal("45.464204"), "longitude": Decimal("9.189982")}
    _refused_by_database(page, **coordinates)
    _refused_by_clean(page, "latitude", **coordinates)
    _refused_by_clean(page, "online_url", latitude=None, longitude=None, online_url="")


def test_an_online_link_is_https() -> None:
    page = EventPageFactory.create(online=True)
    _refused_by_clean(page, "online_url", online_url="http://meet.example.com/fair")


def test_fairs_need_an_attendance_mode() -> None:
    page = EventPageFactory.create()
    _refused_by_database(page, attendance="")
    _refused_by_clean(page, "attendance", attendance="")


def test_six_decimal_places_are_kept_exactly() -> None:
    page = EventPageFactory.create(located=True)
    page.refresh_from_db()
    assert page.latitude == Decimal("45.464204")
    assert page.longitude == Decimal("9.189982")
    assert str(page.latitude) == "45.464204"


def test_geocoding_confidence_is_between_zero_and_one() -> None:
    page = EventPageFactory.create(located=True)
    _refused_by_database(page, geo_confidence=1.5)
    _refused_by_clean(page, "geo_confidence", geo_confidence=-0.1)
