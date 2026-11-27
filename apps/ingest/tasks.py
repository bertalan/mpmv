"""Background jobs of the ingest app (ADR-0004, docs/dev/background-jobs.md)."""

import logging

from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.core.tasks import NO_RETRY, locked, periodic, task

from . import services

logger = logging.getLogger(__name__)


@periodic("*/15 * * * *")
@locked()
@task("ingest.publish_ready_drafts", retry=NO_RETRY)
def publish_ready_drafts(timestamp: int) -> None:
    """Every 15 minutes: publish the ready drafts of the sources that allow writes (ING.7)."""
    ctx = AuditContext(actor_label="system:ingest-publish", source=AuditLog.Source.TASK)
    report = services.publish_ready_drafts(ctx=ctx, dry_run=False)
    for sku, reason in report.skipped.items():
        logger.warning("ingest.publish_ready_drafts: draft %s not published: %s", sku, reason)
    logger.info(
        "ingest.publish_ready_drafts: %d published, %d skipped",
        len(report.published),
        len(report.skipped),
    )
