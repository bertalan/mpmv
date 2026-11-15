"""Background jobs of the accounts app (ADR-0004, docs/dev/background-jobs.md)."""

import logging

from apps.core.tasks import NO_RETRY, locked, periodic, task

from .session_store import SessionStore

logger = logging.getLogger(__name__)


@periodic("37 4 * * *")
@locked()
@task("accounts.clear_sessions", retry=NO_RETRY)
def clear_sessions(timestamp: int) -> None:
    """Every night: `clearsessions` (expired rows of the session table) and the session index
    rows left without a live session (CORE.24)."""
    SessionStore.clear_expired()
    logger.info("accounts.clear_sessions done")
