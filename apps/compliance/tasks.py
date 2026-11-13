"""Background jobs of the compliance app (ADR-0004, docs/dev/background-jobs.md)."""

import logging

from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.core.tasks import NO_RETRY, locked, periodic, task

from . import retention, services

logger = logging.getLogger(__name__)


@periodic("5 * * * *")
@locked()
@task("compliance.flag_overdue_safety_cases", retry=NO_RETRY)
def flag_overdue_safety_cases(timestamp: int) -> None:
    """Every hour: alert staff about each GPSR safety case past its working-day deadline
    (spec 11 CMP.3, GPSR art. 22). Each case is flagged once, with an error in the log."""
    changes = services.flag_overdue_safety_cases()
    logger.info("compliance.flag_overdue_safety_cases flagged %d case(s)", len(changes.changes))


@periodic("10 * * * *")
@locked()
@task("compliance.apply_due_terminations", retry=NO_RETRY)
def apply_due_terminations(timestamp: int) -> None:
    """Every hour: close each vendor whose notice of termination has run out (spec 11 CMP.5, P2B
    art. 4). Never before the notice's effective time; each closure goes through the vendor
    closure service and is audited."""
    ctx = AuditContext(actor_label="system:termination", source=AuditLog.Source.SYSTEM)
    changes = services.apply_due_terminations(ctx=ctx)
    logger.info("compliance.apply_due_terminations made %d change(s)", len(changes.changes))


@periodic("15 3 * * *")
@locked()
@task("compliance.check_published_gpsr", retry=NO_RETRY)
def check_published_gpsr(timestamp: int) -> None:
    """Every night: flag each published product whose GPSR data is incomplete (spec 11 CMP.3),
    with an error in the log and one email a day to the staff alert address."""
    rows = services.flag_published_products_failing_gpsr()
    logger.info("compliance.check_published_gpsr found %d product(s)", len(rows))


@periodic("45 3 * * *")
@locked()
@task("compliance.run_retention", retry=NO_RETRY)
def run_retention(timestamp: int) -> None:
    """Every night: apply the order and account retention lifecycle (CMP.4, D-29, D-30)."""
    ctx = AuditContext(actor_label="system:retention", source=AuditLog.Source.TASK)
    result = retention.run_retention(ctx=ctx)
    logger.info("compliance.run_retention %s", result.counts())
