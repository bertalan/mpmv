# Background jobs and schedules

Background jobs, cron schedules, retries and locks run on PostgreSQL with
[Procrastinate](https://procrastinate.readthedocs.io/) ([ADR-0004](../adr/0004-task-queue.md),
backlog ARC.9). Application code never imports Procrastinate: it goes through
`apps.core.tasks`, and an import-linter contract (`make imports`) rejects any other import.

## Running the worker locally

```sh
make db       # local PostgreSQL (Apple container)
make worker   # applies migrations, then runs `manage.py procrastinate worker`
```

The worker runs queued jobs and, in the same process, the scheduler that queues periodic jobs.
Stop it with Ctrl+C: it finishes the running job first. Within five minutes of starting, the
`core.heartbeat` job runs and the worker logs a line such as
`core.heartbeat beat 1 for the 2026-11-07T10:05:00+01:00 tick`.

Where to look:

- **Admin**: *Procrastinate → Jobs* lists every job with its status, attempts and events
  (read-only).
- **Database**: the `core_heartbeat` row named `core.heartbeat` holds the number of beats and
  the time of the last one; monitoring can alert when it gets old.
- **CLI**: `uv run python manage.py procrastinate healthchecks` checks the connection and the
  queue tables; `uv run python manage.py procrastinate --help` lists the other commands.

STAGE and LIVE run the same command in a dedicated `worker` container (ARC.5, ADR-0005).

## Declaring a job

Jobs live in `apps/<app>/tasks.py`; the worker imports the `tasks` module of every installed
app. Names are stable identifiers, `<app>.<job>`, because they are stored in queued rows.

```python
from apps.core.tasks import enqueue, locked, periodic, task

from . import services


@periodic("30 7 * * *")  # 07:30 Europe/Rome, all year round
@locked()  # never overlaps itself, never piles up
@task("ingest.stock")
def sync_stock(timestamp: int) -> None:
    services.sync_stock(dry_run=False)


@task("orders.send_confirmation")
def send_confirmation(order_id: int) -> None: ...


# In a service, inside the transaction that creates the order:
enqueue(send_confirmation, order_id=order.pk)
```

Rules:

- **Thin jobs.** A job is an adapter like a view or a management command: it calls a service,
  which holds the logic, the dry-run and the audit.
- **JSON arguments.** Pass ids and plain values, never model instances.
- **Transactions.** `enqueue` writes the job in the caller's transaction: if it rolls back, the
  job is never queued, and a job never runs before its data is committed.
- **Idempotent jobs.** A job can run more than once (retry after a crash, manual replay).

## Schedules

`@periodic(cron)` reads the cron expression as wall-clock time in `settings.BUSINESS_TIME_ZONE`
(Europe/Rome), so `30 7 * * *` stays at 07:30 across daylight saving time changes; Procrastinate
alone would read it in UTC. The job receives `timestamp`, the Unix time of the tick. Each tick
is queued once even with several workers. A worker that starts within 10 minutes of a missed
tick still queues it; older ticks are skipped. `schedules()` lists what the scheduler sees.

## Retries

Failed jobs are retried with exponential backoff: by default 5 retries after 30 s, 1, 2, 4 and
8 minutes (`DEFAULT_RETRY`). Pass `retry=Retry(...)` to `@task` to change it, or
`retry=NO_RETRY` for jobs that are better skipped than repeated, such as the heartbeat. After
the last retry the job stays `failed` in the admin.

## Locks

`@locked()` sets both locks of Procrastinate to the job name (or the key you pass):

- **lock**: two jobs with the same key never run at the same time;
- **queueing lock**: while one job with the key is waiting, `enqueue` and the scheduler do not
  queue another; `enqueue` returns `None` instead of a job id.

Use a narrower key for per-object exclusivity, for example `@locked("ingest.stock:vendor-a")`
on a job declared per source.

## Testing jobs

Integration tests run the worker in the test process with
`run_worker(wait=False, scheduler=False)`: it runs the jobs that are due and returns.
`defer_periodic(at)` queues the periodic jobs due at a given instant, as the scheduler does.
The worker uses its own database connections, so these tests are transactional
(`@pytest.mark.django_db(transaction=True)`) and empty the queue tables, which Django does not
manage, before and after each test. See `apps/core/tests/integration/test_tasks.py`.
