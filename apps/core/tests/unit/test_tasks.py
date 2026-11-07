"""Task-queue wrapper without a database: retry backoff and business-time schedules (ARC.9)."""

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from apps.core.tasks import DEFAULT_RETRY, NO_RETRY, Retry, next_run, periodic, schedules, task

ROME = ZoneInfo("Europe/Rome")


@periodic("30 7 * * *")
@task("tests.core.unit_morning")
def morning_job(timestamp: int) -> None:
    """A schedule at 07:30 business time, to check how the registry reads cron expressions."""


def test_default_retry_backs_off_exponentially() -> None:
    assert [DEFAULT_RETRY.delay_after(attempts) for attempts in range(5)] == [30, 60, 120, 240, 480]


def test_default_retry_gives_up_after_five_retries() -> None:
    assert DEFAULT_RETRY.delay_after(5) is None


def test_retry_delay_is_capped() -> None:
    policy = Retry(max_attempts=20, base_delay=60, max_delay=600)
    assert policy.delay_after(10) == 600


def test_no_retry_never_retries() -> None:
    assert NO_RETRY.delay_after(0) is None


def test_retry_refuses_negative_settings() -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        Retry(max_attempts=-1)
    with pytest.raises(ValueError, match="delay"):
        Retry(base_delay=-1)


@pytest.mark.parametrize(
    ("after", "expected"),
    [
        # Winter (CET, UTC+1): 07:30 in Rome is 06:30 UTC.
        (
            dt.datetime(2026, 1, 10, 12, 0, tzinfo=dt.UTC),
            dt.datetime(2026, 1, 11, 6, 30, tzinfo=dt.UTC),
        ),
        # Summer (CEST, UTC+2): 07:30 in Rome is 05:30 UTC.
        (
            dt.datetime(2026, 7, 10, 12, 0, tzinfo=dt.UTC),
            dt.datetime(2026, 7, 11, 5, 30, tzinfo=dt.UTC),
        ),
        # Across the spring DST switch (night of 28-29 March 2026).
        (
            dt.datetime(2026, 3, 28, 12, 0, tzinfo=dt.UTC),
            dt.datetime(2026, 3, 29, 5, 30, tzinfo=dt.UTC),
        ),
        # Across the autumn DST switch (night of 24-25 October 2026).
        (
            dt.datetime(2026, 10, 24, 12, 0, tzinfo=dt.UTC),
            dt.datetime(2026, 10, 25, 6, 30, tzinfo=dt.UTC),
        ),
    ],
)
def test_next_run_reads_cron_in_business_time(after: dt.datetime, expected: dt.datetime) -> None:
    result = next_run("30 7 * * *", after)
    assert result == expected
    assert result.astimezone(ROME).hour == 7
    assert result.tzinfo is not None


def test_next_run_refuses_naive_datetimes() -> None:
    with pytest.raises(ValueError, match="aware"):
        next_run("30 7 * * *", dt.datetime(2026, 1, 10, 12, 0))


def test_heartbeat_is_scheduled_every_five_minutes_in_business_time() -> None:
    heartbeat = {s.task_name: s for s in schedules()}["core.heartbeat"]
    assert heartbeat.cron == "*/5 * * * *"
    assert heartbeat.time_zone == "Europe/Rome"


def test_registered_schedules_fire_in_business_time_across_dst() -> None:
    """The registry the worker reads uses Europe/Rome, not the UTC default of the queue."""
    morning = {s.task_name: s for s in schedules()}["tests.core.unit_morning"]
    winter = morning.next_run(dt.datetime(2026, 1, 10, 12, 0, tzinfo=dt.UTC))
    summer = morning.next_run(dt.datetime(2026, 7, 10, 12, 0, tzinfo=dt.UTC))
    assert winter == dt.datetime(2026, 1, 11, 6, 30, tzinfo=dt.UTC)
    assert summer == dt.datetime(2026, 7, 11, 5, 30, tzinfo=dt.UTC)
