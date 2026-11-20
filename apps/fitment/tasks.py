"""Background jobs of the fitment app (ADR-0004, docs/dev/background-jobs.md)."""

import logging

from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.core.tasks import NO_RETRY, locked, periodic, task

from . import garage_cleanup

logger = logging.getLogger(__name__)


@periodic("20 4 * * *")
@locked()
@task("fitment.cleanup_inactive_garage_profiles", retry=NO_RETRY)
def cleanup_inactive_garage_profiles(timestamp: int) -> None:
    """Every night: email the customers whose garage profiles near 24 months without use, and
    delete the profiles whose limit and 30-day notice period have both passed (spec 17 §17.4.5)."""
    ctx = AuditContext(actor_label="system:garage-cleanup", source=AuditLog.Source.TASK)
    changes = garage_cleanup.run_inactivity_cleanup(ctx=ctx)
    logger.info("fitment.cleanup_inactive_garage_profiles made %d change(s)", len(changes.changes))
