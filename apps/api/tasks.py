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


@periodic("12 4 * * *")
@locked()
@task("api.purge_mutation_log", retry=NO_RETRY)
def purge_mutation_log(timestamp: int) -> None:
    """Every night: delete the API mutation log rows older than 12 months (CORE.29, Q-14)."""
    try:
        deleted = services.purge_mutation_log()
    except Exception:
        # Never raise out of the job: the next night retries the same cutoff, and a failure
        # must not stop the worker's other jobs. The log is the alert (OPS.6).
        logger.exception("api.purge_mutation_log failed; rows past their retention remain")
        return
    logger.info("api.purge_mutation_log deleted %d rows past their retention", deleted)
