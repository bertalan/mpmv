"""Background jobs of the events app (ADR-0004, docs/dev/background-jobs.md)."""

import logging

from apps.core.tasks import NO_RETRY, locked, periodic, task

from . import services

logger = logging.getLogger(__name__)


@periodic("30 8 * * *")
@locked()
@task("events.review_digest", retry=NO_RETRY)
def review_digest(timestamp: int) -> None:
    """Every morning: email the "Event moderators" the events awaiting review (EVT.11).

    Nothing is sent while the queue is empty; each moderator gets at most one a day."""
    changes = services.send_review_digest()
    logger.info("events.review_digest queued %d email(s)", len(changes.changes))
