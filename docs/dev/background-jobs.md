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

STAGE and LIVE run the same command in a dedicated `worker` container (ARC.5, [ADR-0007](../adr/0007-hosting-hetzner-self-managed-postgres.md)).

## Declaring a job

Jobs live in `apps/<app>/tasks.py`; the worker imports the `tasks` module of every installed
app. Names are stable identifiers, `<app>.<job>`, because they are stored in queued rows.

```python
from apps.core.tasks import enqueue, locked, periodic, task

from . import services


@periodic("30 7 * * *")  # 07:30 Europe/Rome, summer and winter time
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
tick still queues it; older ticks are skipped. `schedules()` lists what the scheduler sees, and
`Schedule.ticks(since, until)` lists the ticks it would queue in a period.

Jobs on `main` today (cron in Europe/Rome; a new job adds its row here):

| Job | Schedule | Retry | What it does |
|---|---|---|---|
| `core.heartbeat` | `*/5 * * * *` | none | Counts a beat in `core_heartbeat`, so monitoring sees the worker is alive (ARC.9) |
| `core.purge_throttles` | `17 4 * * *` | none | Deletes throttle counters whose lock and quiet period are over (CORE.5; also CORE.20 blocks) |
| `api.purge_refused_keys` | `22 4 * * *` | none | Deletes refused-key rows older than the window, of every IP (CORE.20) |
| `core.purge_geocode_cache` | `27 4 * * *` | none | Deletes geocoding cache entries past their 180 days (EVT.12) |
| `tax.check_profile_consistency` | `41 5 * * *` | none | Logs current tax profiles that disagree with the vendor's fiscal regime (TAX.2) |
| `vendors.check_establishments` | `47 5 * * *` | none | Logs active vendors established outside the allowed vendor countries (CMP.13); suspends nobody |
| `vendors.purge_unconfirmed_applications` | `23 * * * *` | none | Deletes "Sell with us" applications nobody confirmed (account, applicant vendor, memberships) `VENDOR_APPLICATION_RETENTION_DAYS` (3) days after the request; audited by count only, one entry per batch of 200; each row is locked and re-checked before deletion, so a confirming applicant is never deleted; a deletion can lag the 3 days by up to an hour (VEN.22, D-24) |
| `vendors.purge_retained_logos` | `53 3 * * *` | none | Deletes the previous logo files kept for a staff revert once `VENDOR_LOGO_REVIEW_DAYS` (14) days have passed since the change, unless staff confirmed, reverted or already purged it or a vendor uses the file now; one `vendor.logo_purged` audit entry per file (CORE.9, owner decision Q1) |
| `vendors.remind_unconfirmed_applications` | `41 * * * *` | none | Sends the one reminder (with the confirmation and cancel links) `VENDOR_APPLICATION_REMINDER_HOURS` (48) hours after the request of an unconfirmed "Sell with us" application; exactly one, idempotent, batches of 200 with a lock and re-check per row (VEN.22, D-24) |
| `cms.publish_scheduled` | `*/5 * * * *` | none | Publishes pages whose go-live has come and unpublishes expired ones (Wagtail's `publish_scheduled`), so scheduled blog articles appear on time (CMS.3) |
| `compliance.flag_overdue_safety_cases` | `5 * * * *` | none | Alerts staff about each open GPSR safety case past its working-day deadline: an error in the log, one email to the operator's compliance address (else its support address) and an audit entry (CMP.3). Each case is flagged once (`overdue_alerted_at`), so reruns send nothing new |
| `compliance.apply_due_terminations` | `10 * * * *` | none | Closes each vendor whose notice of termination has run out (`effective_at` reached, never earlier) through the vendor closure service and marks the notice applied; a vendor already closed just has its notice marked applied (CMP.5 part 4, P2B art. 4) |
| `compliance.run_retention` | `45 3 * * *` | none | The retention lifecycle (CMP.4, D-29, D-30): orders past their active phase go to `restricted_tax_only`, past `tax_retention_until` to `anonymized`, pending account erasures complete; through the ORD.2 scrub services, one `compliance.retention_run` audit entry with counts only |
| `compliance.check_published_gpsr` | `15 3 * * *` | none | Logs one error per published product whose GPSR data is incomplete and enqueues at most one email a day to the staff alert address (idempotency key per business date) (CMP.3); unpublishes nothing |
| `core.send_email` | on demand | 5 retries, 1 min doubling to 1 h | Sends one outbox email ([transactional-email.md](transactional-email.md), ARC.16) |
| `tax.recheck_vat_number` | on demand | 8 retries, 1 min doubling to 1 h | Asks VIES again about a VAT number while VIES is down (TAX.5) |

The two daylight saving time nights follow cron's rules:

- **Spring** (02:00-02:59 does not exist): ticks in that hour run at 03:00, late but not lost.
- **Autumn** (02:00-02:59 happens twice): a schedule that names its hours, such as
  `30 2 * * *`, `0 1-3 * * *` or `0 */2 * * *`, fires on the first pass only, so a nightly job
  never runs twice. A schedule whose hour field is `*`, such as `*/5 * * * *`, follows real
  time and fires on both passes.

## Retries

Failed jobs are retried with exponential backoff: by default 5 retries after 30 s, 1, 2, 4 and
8 minutes (`DEFAULT_RETRY`). Pass `retry=Retry(...)` to `@task` to change it, or
`retry=NO_RETRY` for jobs that are better skipped than repeated, such as the heartbeat. After
the last retry the job stays `failed` in the admin.

## Locks

`@locked()` sets both locks of Procrastinate to the job name (or the key you pass):

- **lock**: two jobs with the same key never run at the same time; a job queued while another
  runs waits for it to finish;
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
