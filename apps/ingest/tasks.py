"""Background jobs of the ingest app (ADR-0004, docs/dev/background-jobs.md)."""

import datetime as dt
import logging

from django.utils import timezone

from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.core.tasks import NO_RETRY, business_tz, locked, periodic, task

from . import reports, sync
from .models import ImportRun, SupplierSource

logger = logging.getLogger(__name__)


def _run_all(kind: str) -> tuple[int, int]:
    """Sync every active source, one at a time; returns (sources run, sources that failed).
    `run_sync` already closes its run as failed and never raises; the guard here is the second
    line, so that nothing one source does can stop the sources after it."""
    ctx = AuditContext(actor_label=f"system:ingest-{kind}", source=AuditLog.Source.TASK)
    now = timezone.now()
    sources = SupplierSource.objects.filter(is_active=True).select_related("vendor").order_by("pk")
    count = failed = 0
    for source in sources:
        count += 1
        try:
            run = sync.run_sync(source, kind, ctx=ctx, now=now)
        except Exception:
            logger.exception("ingest.%s_sync: source %s raised", kind, source.code)
            failed += 1
            continue
        if run is None or run.status != ImportRun.Status.SUCCEEDED:
            failed += 1
    return count, failed


def _report(job: str, kind: str) -> None:
    count, failed = _run_all(kind)
    logger.info("ingest.%s ran %d source(s), %d failed", job, count, failed)


@periodic("30 8 * * *")
@locked()
@task("ingest.stock_sync", retry=NO_RETRY)
def stock_sync(timestamp: int) -> None:  # `timestamp` is required by the scheduler
    """Every morning, after the scrape: apply each active source's supplier stock (ING.8).

    Spec 08 puts the scrape at about 06:20 UTC and this sync at 07:30 UTC. The schedule is read in
    Rome time, so 08:30 is 07:30 UTC in winter and 06:30 UTC in summer: after the scrape either way.
    There is no `ingest.scrape` job yet; when it exists this should follow its completion.

    A source writes only with its per-source write flag, otherwise the run is a dry run. A 404
    sets stock to 0, stale data opens a review item and is never applied. Never raises: a source
    that fails ends its own run as failed and the next source goes on."""
    _report("stock_sync", ImportRun.Kind.STOCK)


@periodic("0 9 * * *")
@locked()
@task("ingest.price_sync", retry=NO_RETRY)
def price_sync(timestamp: int) -> None:  # `timestamp` is required by the scheduler
    """Every morning, half an hour after the stock sync: apply each active source's supplier prices
    as net prices (ING.8).

    A change above 15 percent, a sale and stale data open a review item and write nothing; a
    source writes only with its per-source write flag, otherwise the run is a dry run."""
    _report("price_sync", ImportRun.Kind.PRICE)


@periodic("10 0 * * *")
@locked()
@task("ingest.daily_report", retry=NO_RETRY)
def daily_report(timestamp: int) -> None:  # `timestamp` is the scheduled time
    """Every night: email staff the report of the previous business day, whole (ING.10).

    It runs just after business midnight (Rome time, which follows the daylight saving changes)
    and covers the day that just ended from its first to its last minute, a 23 or 25 hour day on
    a time change. Never raises; a second run for the same day sends nothing."""
    local = dt.datetime.fromtimestamp(timestamp, tz=business_tz())
    day = local.date() - dt.timedelta(days=1)
    ctx = AuditContext(actor_label="system:ingest-daily-report", source=AuditLog.Source.TASK)
    try:
        sent = reports.send_daily_report(day, ctx=ctx)
    except Exception:
        logger.exception("ingest.daily_report for %s failed", day)
        return
    logger.info("ingest.daily_report for %s: sent=%s", day, sent)
