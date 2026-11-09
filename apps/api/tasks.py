"""Background jobs of the api app (ADR-0004, docs/dev/background-jobs.md)."""

import logging

from apps.core.tasks import NO_RETRY, locked, periodic, task

from . import services

logger = logging.getLogger(__name__)


@periodic("22 4 * * *")
@locked()
@task("api.purge_refused_keys", retry=NO_RETRY)
def purge_refused_keys(timestamp: int) -> None:
    """Every night: delete the refused-key rows older than the window, of every IP (CORE.20).

    Each 401 cleans only its own IP bucket, so buckets that stop refusing leave rows behind.
    """
    deleted = services.purge_expired_refused_keys()
    logger.info("api.purge_refused_keys deleted %d expired rows", deleted)
