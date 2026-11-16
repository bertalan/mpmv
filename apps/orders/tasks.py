"""Background jobs of the orders app (ADR-0004, docs/dev/background-jobs.md)."""

import logging

from apps.core.tasks import NO_RETRY, locked, periodic, task

from . import amendments

logger = logging.getLogger(__name__)


@periodic("*/5 * * * *")
@locked()
@task("orders.discard_idle_amendment_drafts", retry=NO_RETRY)
def discard_idle_amendment_drafts(timestamp: int) -> None:
    """Every 5 minutes: discard the amendment drafts idle for an hour (ORD.20, spec 07
    Concurrency), so a forgotten draft never blocks its package. Idempotent."""
    done = amendments.discard_idle_drafts()
    logger.info("orders.discard_idle_amendment_drafts: %d discarded", done)
