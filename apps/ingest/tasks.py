"""Background jobs of the ingest app (ADR-0004, docs/dev/background-jobs.md)."""

import logging

from django.utils import timezone

from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.core.tasks import NO_RETRY, locked, periodic, task

from . import sync
from .models import ImportRun, SupplierSource

logger = logging.getLogger(__name__)


def _run_all(kind: str) -> int:
    ctx = AuditContext(actor_label=f"system:ingest-{kind}", source=AuditLog.Source.TASK)
    now = timezone.now()
    sources = SupplierSource.objects.filter(is_active=True).select_related("vendor").order_by("pk")
    count = 0
    for source in sources:
        sync.run_sync(source, kind, ctx=ctx, now=now)
        count += 1
    return count


@periodic("30 8 * * *")
@locked()
@task("ingest.stock_sync", retry=NO_RETRY)
def stock_sync(timestamp: int) -> None:
    """Every morning, after the scrape: apply each active source's supplier stock (ING.8).

    Spec 08 puts the scrape at about 06:20 UTC and this sync at 07:30 UTC. The schedule is read in
    Rome time, so 08:30 is 07:30 UTC in winter and 06:30 UTC in summer: after the scrape either way.
    There is no `ingest.scrape` job yet; when it exists this should follow its completion.

    A source writes only with its per-source write flag, otherwise the run is a dry run. A 404
    sets stock to 0, stale data opens a review item and is never applied."""
    logger.info("ingest.stock_sync ran %d source(s)", _run_all(ImportRun.Kind.STOCK))


@periodic("0 9 * * *")
@locked()
@task("ingest.price_sync", retry=NO_RETRY)
def price_sync(timestamp: int) -> None:
    """Every morning, half an hour after the stock sync: apply each active source's supplier prices
    as net prices (ING.8).

    A change above 15 percent, a sale and stale data open a review item and write nothing; a
    source writes only with its per-source write flag, otherwise the run is a dry run."""
    logger.info("ingest.price_sync ran %d source(s)", _run_all(ImportRun.Kind.PRICE))
