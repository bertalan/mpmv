"""Background jobs, schedules, retries and locks (ADR-0004).

This is the only module that imports Procrastinate (an import-linter contract enforces it), so
moving to another queue later means changing this file only. Application code:

- declares a job with `@task("<app>.<job>")` in `apps/<app>/tasks.py`; the worker imports
  every `tasks` module of the installed apps;
- runs it on a cron schedule with `@periodic("<cron>")`, read as wall-clock time in
  `settings.BUSINESS_TIME_ZONE` (Europe/Rome), with cron's daylight saving time rules (see
  `_BusinessTimeCron`); the job receives `timestamp`, the Unix time of the tick;
- prevents overlaps with `@locked()`: at most one run at a time and one job waiting;
- queues it with `enqueue(job, **kwargs)`, inside the caller's database transaction.

Job arguments must be JSON-serializable. Failed jobs are retried with exponential backoff
(`DEFAULT_RETRY`) unless the job says otherwise. See docs/dev/background-jobs.md.
"""

import asyncio
import contextlib
import datetime as dt
import functools
import logging
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Any, ParamSpec, TypeVar
from zoneinfo import ZoneInfo

import croniter
from django.conf import settings
from django.db import transaction
from procrastinate import App, BaseRetryStrategy, RetryDecision
from procrastinate.contrib.django import app as _proxy_app
from procrastinate.contrib.django.django_connector import DjangoConnector
from procrastinate.exceptions import AlreadyEnqueued
from procrastinate.jobs import Job as _QueueRow
from procrastinate.periodic import PeriodicDeferrer, PeriodicTask
from procrastinate.tasks import Task

from . import mail, services

P = ParamSpec("P")
R = TypeVar("R")
A = ParamSpec("A")

#: A declared job, as returned by `task`: call it to run it inline, `enqueue` it to queue it.
type Job = Task[..., Any, ...]

logger = logging.getLogger(__name__)


# --- Retries -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Retry:
    """Retry policy: exponential backoff from `base_delay` seconds, capped at `max_delay`."""

    max_attempts: int = 5  # retries after the first run; 0 means never retry
    base_delay: int = 30
    max_delay: int = 3600

    def __post_init__(self) -> None:
        if self.max_attempts < 0:
            raise ValueError("max_attempts must not be negative")
        if self.base_delay < 0 or self.max_delay < 0:
            raise ValueError("a retry delay must not be negative")

    def delay_after(self, attempts: int) -> int | None:
        """Seconds before the next run after `attempts` earlier retries; None means give up."""
        if attempts >= self.max_attempts:
            return None
        return int(min(self.base_delay * 2**attempts, self.max_delay))


DEFAULT_RETRY = Retry()
NO_RETRY = Retry(max_attempts=0)

#: How far the worker's clock may run ahead of the database's for an immediate retry.
_CLOCK_SKEW = dt.timedelta(seconds=1)


class _BackoffStrategy(BaseRetryStrategy):
    def __init__(self, policy: Retry) -> None:
        self.policy = policy

    def get_retry_decision(
        self, *, exception: BaseException, job: _QueueRow
    ) -> RetryDecision | None:
        delay = self.policy.delay_after(job.attempts)
        if delay is None:
            return None
        if delay == 0:
            # Procrastinate stamps "now" with the worker's clock; a database clock a few
            # milliseconds behind would then keep the job waiting. Date it slightly in the past.
            return RetryDecision(retry_at=dt.datetime.now(dt.UTC) - _CLOCK_SKEW)
        return RetryDecision(retry_in={"seconds": delay})


# --- Business-time schedules ----------------------------------------------------------------


def business_tz() -> ZoneInfo:
    return ZoneInfo(settings.BUSINESS_TIME_ZONE)


class _BusinessTimeCron:
    """The cron iterator of a schedule: `cron` read as wall-clock time in `tz`.

    Daylight saving time follows cron's rules. On the spring night the ticks of the skipped
    hour (02:00-02:59 in Europe/Rome) move to 03:00, so they are late, never lost. On the
    autumn night 02:00-02:59 happens twice: a schedule that names its hours (`30 2 * * *`,
    `0 1-3 * * *`, `0 */2 * * *`) fires on the first pass only, while one that runs every hour
    (hour field `*`, such as `*/5 * * * *`) follows real time and fires on both.

    It offers the part of the croniter interface that Procrastinate's scheduler uses.
    """

    def __init__(self, cron: str, tz: dt.tzinfo) -> None:
        self._iterator = croniter.croniter(cron, dt.datetime.now(tz))
        self.tzinfo = tz
        self._names_its_hours = str(self._iterator.expressions[1]) != "*"

    def set_current(self, start_time: float) -> float:
        self._iterator.set_current(start_time=start_time)
        return start_time

    def get_next(self, ret_type: type = float, start_time: float | None = None) -> float:
        if start_time is not None:
            self._iterator.set_current(start_time=start_time)
        return self._first_pass(lambda: float(self._iterator.get_next(ret_type=float)))

    def get_prev(self, ret_type: type = float) -> float:
        return self._first_pass(lambda: float(self._iterator.get_prev(ret_type=float)))

    def _first_pass(self, step: Callable[[], float]) -> float:
        tick = step()
        while self._names_its_hours and dt.datetime.fromtimestamp(tick, self.tzinfo).fold:
            tick = step()  # the second pass of a repeated wall-clock time: already fired
        return tick


def _next_tick(iterator: _BusinessTimeCron, after: dt.datetime) -> dt.datetime:
    if after.utcoffset() is None:
        raise ValueError("after must be a timezone-aware datetime")
    iterator.set_current(start_time=after.timestamp())
    return dt.datetime.fromtimestamp(iterator.get_next(ret_type=float), tz=dt.UTC)


def next_run(cron: str, after: dt.datetime) -> dt.datetime:
    """First tick of `cron`, read in business time, strictly after `after` (returned in UTC)."""
    return _next_tick(_BusinessTimeCron(cron, business_tz()), after)


class _BusinessTimePeriodicTask(PeriodicTask[..., Any, ...]):
    """A periodic task whose cron expression is read in business time (the queue assumes UTC)."""

    @functools.cached_property
    def croniter(self) -> Any:  # read by Procrastinate's scheduler
        return _BusinessTimeCron(self.cron, business_tz())


@dataclass(frozen=True)
class Schedule:
    task_name: str
    periodic_id: str
    cron: str
    time_zone: str  # the zone the scheduler reads `cron` in

    def next_run(self, after: dt.datetime) -> dt.datetime:
        return _next_tick(_BusinessTimeCron(self.cron, ZoneInfo(self.time_zone)), after)

    def ticks(self, since: dt.datetime, until: dt.datetime) -> list[dt.datetime]:
        """The ticks a running scheduler queues after `since`, up to `until` (in UTC)."""
        if since.utcoffset() is None or until.utcoffset() is None:
            raise ValueError("since and until must be timezone-aware datetimes")
        registry = _ready_app().periodic_registry
        registered = registry.periodic_tasks[(self.task_name, self.periodic_id)]
        # A fresh copy, so that the live scheduler's iterator is left alone.
        periodic_task = _BusinessTimePeriodicTask(
            task=registered.task, cron=self.cron, periodic_id=self.periodic_id, configure_kwargs={}
        )
        timestamps = PeriodicDeferrer(registry=registry).get_timestamps(
            periodic_task=periodic_task, since=int(since.timestamp()), until=until.timestamp()
        )
        return [dt.datetime.fromtimestamp(timestamp, tz=dt.UTC) for timestamp in timestamps]


# --- The application ------------------------------------------------------------------------

# Django creates the Procrastinate app in AppConfig.ready(), after importing the `tasks` modules.
# Schedules declared before that are kept here and registered by `on_app_ready`.
_app: App | None = None
_pending_schedules: list[tuple[Job, str, str]] = []


def on_app_ready(app: App) -> None:
    """Hook called by Procrastinate once its Django app exists (PROCRASTINATE_ON_APP_READY)."""
    global _app
    _app = app
    for job, cron, periodic_id in _pending_schedules:
        _register_schedule(app, job, cron, periodic_id)
    _pending_schedules.clear()


def _ready_app() -> App:
    if _app is None:
        raise RuntimeError("the task queue is not ready: Django has not finished loading apps")
    return _app


def _register_schedule(app: App, job: Job, cron: str, periodic_id: str) -> None:
    registry = app.periodic_registry
    registry.register_task(task=job, cron=cron, periodic_id=periodic_id, configure_kwargs={})
    registry.periodic_tasks[(job.name, periodic_id)] = _BusinessTimePeriodicTask(
        task=job, cron=cron, periodic_id=periodic_id, configure_kwargs={}
    )


@contextlib.contextmanager
def _worker_connection(app: App) -> Iterator[App]:
    """Use a dedicated connection pool, as `manage.py procrastinate worker` does."""
    connector = app.connector
    if isinstance(connector, DjangoConnector):
        with app.replace_connector(connector.get_worker_connector()):
            yield app
    else:
        yield app


# --- Declaring jobs -------------------------------------------------------------------------


def task(
    name: str, *, queue: str = "default", retry: Retry = DEFAULT_RETRY
) -> Callable[[Callable[P, R]], Task[P, R, P]]:
    """Declare a job under a stable `name` such as `ingest.stock` (stored in queued rows)."""
    return _proxy_app.task(name=name, queue=queue, retry=_BackoffStrategy(retry))


def locked(key: str | None = None) -> Callable[[Task[P, R, A]], Task[P, R, A]]:
    """Never run two jobs with the same key at once, nor queue a second one while one waits.

    The key defaults to the job name. Apply it above `@task`.
    """

    def apply(job: Task[P, R, A]) -> Task[P, R, A]:
        job.lock = job.queueing_lock = key or job.name
        return job

    return apply


def periodic(cron: str, *, periodic_id: str = "") -> Callable[[Task[P, R, A]], Task[P, R, A]]:
    """Run the job on `cron`, read in business time. Apply it above `@task` (and `@locked`).

    The job must accept `timestamp: int`, the Unix time of the tick. Use `periodic_id` to
    register the same job on several schedules.
    """
    if not croniter.croniter.is_valid(cron):
        raise ValueError(f"invalid cron expression {cron!r}")

    def apply(job: Task[P, R, A]) -> Task[P, R, A]:
        if _app is None:
            _pending_schedules.append((job, cron, periodic_id))
        else:
            _register_schedule(_app, job, cron, periodic_id)
        return job

    return apply


def schedules() -> list[Schedule]:
    """Every registered periodic job, as the worker's scheduler sees it."""
    return [
        Schedule(
            task_name=periodic_task.task.name,
            periodic_id=periodic_task.periodic_id,
            cron=periodic_task.cron,
            time_zone=str(periodic_task.croniter.tzinfo or "UTC"),
        )
        for periodic_task in _ready_app().periodic_registry.periodic_tasks.values()
    ]


# --- Queueing and inspecting jobs -----------------------------------------------------------


def enqueue(job: Job, /, *, schedule_at: dt.datetime | None = None, **kwargs: Any) -> int | None:
    """Queue `job` with keyword arguments; return the job id.

    The row is written in the caller's transaction, so a rollback drops the job too. Returns
    None, without breaking the transaction, when a `locked` job with the same key is waiting.
    """
    deferrer = job.configure(schedule_at=schedule_at)
    try:
        with transaction.atomic():
            return deferrer.defer(**kwargs)
    except AlreadyEnqueued:
        logger.info("%s not queued: a job with lock %r is waiting", job.name, job.queueing_lock)
        return None


@dataclass(frozen=True)
class JobInfo:
    id: int
    task_name: str
    queue: str
    status: str  # todo, doing, succeeded, failed, cancelled, aborted
    attempts: int
    scheduled_at: dt.datetime | None
    lock: str | None  # set by `@locked`: no two jobs with this key run at once


def _job_info(row: _QueueRow) -> JobInfo:
    if row.id is None or row.status is None:
        raise ValueError("a queued job row always has an id and a status")
    return JobInfo(
        id=row.id,
        task_name=row.task_name,
        queue=row.queue,
        status=row.status,
        attempts=row.attempts,
        scheduled_at=row.scheduled_at,
        lock=row.lock,
    )


def list_jobs(task_name: str | None = None, *, status: str | None = None) -> list[JobInfo]:
    rows = _ready_app().job_manager.list_jobs(task=task_name, status=status)
    return sorted((_job_info(row) for row in rows), key=lambda info: info.id)


def get_job(job_id: int) -> JobInfo:
    rows = list(_ready_app().job_manager.list_jobs(id=job_id))
    if not rows:
        raise LookupError(f"no job with id {job_id}")
    return _job_info(rows[0])


# --- Running the worker ---------------------------------------------------------------------


def defer_periodic(at: dt.datetime | None = None) -> None:
    """Queue the periodic jobs due at `at` (default: now), exactly as the worker's scheduler does.

    Safe to repeat: the queue accepts each tick of each schedule only once.
    """
    app = _ready_app()
    moment = (at or dt.datetime.now(dt.UTC)).timestamp()
    deferrer = PeriodicDeferrer(registry=app.periodic_registry, **app.periodic_defaults)

    async def run() -> None:
        async with app.open_async():
            await deferrer.defer_jobs(deferrer.get_previous_tasks(at=moment))

    with _worker_connection(app):
        asyncio.run(run())


def run_worker(
    *, queues: Iterable[str] | None = None, wait: bool = True, scheduler: bool = True
) -> None:
    """Run a worker in this process; `make worker` runs `manage.py procrastinate worker` instead.

    With `wait=False` it runs the jobs that are due, then returns (tests, manual catch-up). With
    `scheduler=False` it leaves the periodic schedules alone and only runs queued jobs.
    """
    app = _ready_app()
    options: dict[str, Any] = {"wait": wait, "listen_notify": wait, "install_signal_handlers": wait}
    if queues is not None:
        options["queues"] = list(queues)
    registry = app.periodic_registry
    registered = registry.periodic_tasks
    if not scheduler:
        registry.periodic_tasks = {}  # the worker's scheduler then finds nothing to defer
    try:
        with _worker_connection(app):
            app.run_worker(**options)
    finally:
        registry.periodic_tasks = registered


# --- Jobs of the core app -------------------------------------------------------------------


@periodic("*/5 * * * *")
@locked()
@task("core.heartbeat", retry=NO_RETRY)
def heartbeat(timestamp: int) -> None:
    """Every five minutes: prove that the worker and its scheduler run (see `core.Heartbeat`)."""
    scheduled_for = dt.datetime.fromtimestamp(timestamp, tz=dt.UTC)
    beat = services.record_heartbeat("core.heartbeat", scheduled_for=scheduled_for)
    logger.info(
        "core.heartbeat beat %d for the %s tick",
        beat.beats,
        scheduled_for.astimezone(business_tz()).isoformat(),
    )


@periodic("17 4 * * *")
@locked()
@task("core.purge_throttles", retry=NO_RETRY)
def purge_throttles(timestamp: int) -> None:
    """Every night: delete throttle counters whose lock and quiet period are over (CORE.5)."""
    deleted = services.purge_expired_throttles()
    logger.info("core.purge_throttles deleted %d expired rows", deleted)


@periodic("27 4 * * *")
@locked()
@task("core.purge_geocode_cache", retry=NO_RETRY)
def purge_geocode_cache(timestamp: int) -> None:
    """Every night: delete geocoding cache entries past their 180 days (EVT.12)."""
    from .geocoding.services import purge_expired_geocode_cache

    deleted = purge_expired_geocode_cache()
    logger.info("core.purge_geocode_cache deleted %d expired rows", deleted)


@task(
    "core.send_email",
    retry=Retry(
        max_attempts=mail.MAX_RETRIES, base_delay=mail.BASE_DELAY, max_delay=mail.MAX_DELAY
    ),
)
def send_email(email_id: str) -> None:
    """Send one outbox email (ARC.16); a transient failure raises, so the queue retries it."""
    services.deliver_email(email_id)
