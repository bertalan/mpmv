"""Background jobs of the compliance app (ADR-0004, docs/dev/background-jobs.md)."""

import logging

from apps.core.tasks import NO_RETRY, locked, periodic, task

from . import services

logger = logging.getLogger(__name__)


@periodic("5 * * * *")
@locked()
@task("compliance.flag_overdue_safety_cases", retry=NO_RETRY)
def flag_overdue_safety_cases(timestamp: int) -> None:
    """Every hour: alert staff about each GPSR safety case past its working-day deadline
    (spec 11 CMP.3, GPSR art. 22). Each case is flagged once, with an error in the log."""
    changes = services.flag_overdue_safety_cases()
    logger.info("compliance.flag_overdue_safety_cases flagged %d case(s)", len(changes.changes))
