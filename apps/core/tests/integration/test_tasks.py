"""Background jobs on the real PostgreSQL queue, run by an in-process worker (ARC.9, ADR-0004).

Done when: a test job is scheduled and observable. The worker runs in the test process with
`wait=False`: it drains the queue and returns. It uses its own database connections, so the
tests that run it are transactional and empty the queue tables (not managed by Django) by hand.
"""

import datetime as dt
import logging
import uuid
from collections.abc import Iterator
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.utils import timezone

from apps.core.models import Heartbeat
from apps.core.tasks import (
    Retry,
    defer_periodic,
    enqueue,
    get_job,
    heartbeat,
    list_jobs,
    locked,
    run_worker,
    task,
)

ROME = ZoneInfo("Europe/Rome")
QUEUE_TABLES = (
    "procrastinate_jobs, procrastinate_periodic_defers, procrastinate_events, procrastinate_workers"
)

calls: dict[str, int] = {}


@task("tests.core.always_fails")
def always_fails() -> None:
    raise RuntimeError("this job always fails")


@task("tests.core.flaky", retry=Retry(max_attempts=3, base_delay=0))
def flaky(key: str) -> None:
    calls[key] = calls.get(key, 0) + 1
    if calls[key] == 1:
        raise RuntimeError("the first attempt fails")


@task("tests.core.hopeless", retry=Retry(max_attempts=2, base_delay=0))
def hopeless(key: str) -> None:
    calls[key] = calls.get(key, 0) + 1
    raise RuntimeError("this job never succeeds")


@locked()
@task("tests.core.exclusive")
def exclusive(n: int) -> None:
    """A job that must never be queued twice."""


def _purge_queue() -> None:
    with connection.cursor() as cursor:
        cursor.execute(f"TRUNCATE {QUEUE_TABLES} RESTART IDENTITY CASCADE")


@pytest.fixture
def empty_queue() -> Iterator[None]:
    _purge_queue()
    calls.clear()
    yield
    _purge_queue()


def _drain() -> None:
    """Run the jobs that are due now, without the scheduler, then return."""
    run_worker(wait=False, scheduler=False)


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("empty_queue")
def test_enqueued_heartbeat_runs_in_the_worker_and_is_observable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    tick = dt.datetime(2026, 11, 7, 10, 0, tzinfo=ROME)
    job_id = enqueue(heartbeat, timestamp=int(tick.timestamp()))
    assert job_id is not None
    assert get_job(job_id).status == "todo"

    with caplog.at_level(logging.INFO, logger="apps.core.tasks"):
        _drain()

    assert get_job(job_id).status == "succeeded"
    beat = Heartbeat.objects.get(name="core.heartbeat")
    assert beat.beats == 1
    assert beat.scheduled_for == tick
    assert "heartbeat" in caplog.text


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("empty_queue")
def test_scheduler_defers_the_heartbeat_once_per_tick_and_the_worker_runs_it() -> None:
    at = dt.datetime(2026, 11, 7, 10, 2, tzinfo=ROME)
    defer_periodic(at)
    defer_periodic(at)  # a second scheduler pass (or a second worker) must not duplicate it

    jobs = list_jobs("core.heartbeat")
    assert [job.status for job in jobs] == ["todo"]

    _drain()

    assert [job.status for job in list_jobs("core.heartbeat")] == ["succeeded"]
    beat = Heartbeat.objects.get(name="core.heartbeat")
    assert beat.scheduled_for == dt.datetime(2026, 11, 7, 10, 0, tzinfo=ROME)


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("empty_queue")
def test_a_waiting_heartbeat_blocks_the_next_tick() -> None:
    """Queueing lock: if the worker is behind, ticks do not pile up."""
    defer_periodic(dt.datetime(2026, 11, 7, 10, 2, tzinfo=ROME))
    defer_periodic(dt.datetime(2026, 11, 7, 10, 7, tzinfo=ROME))

    jobs = list_jobs("core.heartbeat")
    assert [job.status for job in jobs] == ["todo"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("empty_queue")
def test_a_failing_job_is_retried_later_with_backoff() -> None:
    before = timezone.now()
    job_id = enqueue(always_fails)
    assert job_id is not None

    _drain()

    job = get_job(job_id)
    assert job.status == "todo"
    assert job.attempts == 1
    assert job.scheduled_at is not None
    assert before + dt.timedelta(seconds=30) <= job.scheduled_at
    assert job.scheduled_at <= timezone.now() + dt.timedelta(seconds=30)


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("empty_queue")
def test_a_flaky_job_succeeds_on_retry() -> None:
    key = uuid.uuid4().hex
    job_id = enqueue(flaky, key=key)
    assert job_id is not None

    _drain()

    assert calls[key] == 2
    assert get_job(job_id).status == "succeeded"


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("empty_queue")
def test_a_job_fails_for_good_once_its_retries_are_spent() -> None:
    key = uuid.uuid4().hex
    job_id = enqueue(hopeless, key=key)
    assert job_id is not None

    _drain()

    assert calls[key] == 3  # the first run and two retries
    assert get_job(job_id).status == "failed"


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("empty_queue")
def test_a_job_scheduled_in_the_future_waits() -> None:
    job_id = enqueue(always_fails, schedule_at=timezone.now() + dt.timedelta(hours=1))
    assert job_id is not None

    _drain()

    job = get_job(job_id)
    assert job.status == "todo"
    assert job.attempts == 0


@pytest.mark.django_db
def test_each_heartbeat_updates_a_single_row() -> None:
    first = dt.datetime(2026, 11, 7, 10, 0, tzinfo=ROME)
    second = dt.datetime(2026, 11, 7, 10, 5, tzinfo=ROME)

    heartbeat(timestamp=int(first.timestamp()))
    heartbeat(timestamp=int(second.timestamp()))

    beat = Heartbeat.objects.get(name="core.heartbeat")
    assert beat.beats == 2
    assert beat.scheduled_for == second
    assert beat.last_beat_at is not None


@pytest.mark.django_db
def test_a_locked_job_cannot_be_queued_twice_and_the_transaction_survives() -> None:
    first = enqueue(exclusive, n=1)
    second = enqueue(exclusive, n=2)

    assert first is not None
    assert second is None
    assert [job.id for job in list_jobs("tests.core.exclusive")] == [first]
    # The refused insert was isolated in a savepoint: the caller's transaction still works.
    assert Heartbeat.objects.count() == 0
