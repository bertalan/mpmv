"""ORD.41: `business_time_zone_at(ts)`, the database twin of `business_tz(at)` (ARC.27).

It reads the operator version valid at `ts`; a moment before the first configuration reads the
current one; with no operator it reads the configured zone, which every connection carries as
`mpmv.business_time_zone`, and UTC when that is empty, as `configured_tz()` does."""

import datetime as dt
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from django.db import connection

from apps.core.services import business_tz
from apps.core.tests.factories import PlatformOperatorFactory, utc

pytestmark = pytest.mark.django_db
NEW_YEAR_EVE = dt.datetime(2030, 12, 31, 23, 30, tzinfo=dt.UTC)


def sql_zone(at: dt.datetime | None = None) -> str:
    with connection.cursor() as cursor:
        if at is None:
            cursor.execute("SELECT business_time_zone_at(now())")
        else:
            cursor.execute("SELECT business_time_zone_at(%s)", [at])
        row = cursor.fetchone()
    assert row is not None
    return str(row[0])


def both(at: dt.datetime | None = None) -> tuple[str, str]:
    return business_tz(at).key, sql_zone(at)


@pytest.fixture
def versions() -> None:
    """Europe/Rome until 1 June 2031, UTC afterwards."""
    PlatformOperatorFactory.create(
        valid_from=utc(2020), valid_to=utc(2031, 6), business_time_zone="Europe/Rome"
    )
    PlatformOperatorFactory.create(
        open_ended=True, valid_from=utc(2031, 6), business_time_zone="UTC"
    )


def test_each_moment_reads_the_version_valid_then(versions: None) -> None:
    assert both(NEW_YEAR_EVE) == ("Europe/Rome", "Europe/Rome")
    assert both(utc(2031, 12, 31, 23)) == ("UTC", "UTC")
    assert both(utc(2031, 6)) == ("UTC", "UTC")  # valid_from is inclusive, valid_to exclusive


def test_a_moment_before_the_first_configuration_reads_the_current_zone() -> None:
    PlatformOperatorFactory.create(
        open_ended=True, valid_from=utc(2020), business_time_zone="Asia/Tokyo"
    )
    assert both(utc(2001)) == ("Asia/Tokyo", "Asia/Tokyo")
    assert both() == ("Asia/Tokyo", "Asia/Tokyo")


def test_without_an_operator_the_configured_zone_stands_in(settings: Any) -> None:
    assert both() == ("Europe/Rome", "Europe/Rome")  # the test settings configure Europe/Rome
    settings.BUSINESS_TIME_ZONE = "America/New_York"
    assert both(NEW_YEAR_EVE) == ("America/New_York", "America/New_York")
    settings.BUSINESS_TIME_ZONE = "UTC"
    assert both() == ("UTC", "UTC")


def test_an_empty_connection_setting_means_utc() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('mpmv.business_time_zone', '', true)")
    assert sql_zone() == "UTC"
    assert business_tz() == ZoneInfo("Europe/Rome")  # Python still reads its own setting


def test_an_unknown_configured_zone_is_never_handed_to_the_database(settings: Any) -> None:
    """core.E931 stops every command first; the connection then simply falls back to UTC."""
    settings.BUSINESS_TIME_ZONE = "Europe/Atlantis"
    assert sql_zone() == "UTC"


def test_the_function_is_stable_not_immutable() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT provolatile FROM pg_proc WHERE proname = 'business_time_zone_at'")
        assert cursor.fetchall() == [("s",)]
