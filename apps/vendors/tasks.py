"""Background jobs of the vendors app (ADR-0004, docs/dev/background-jobs.md)."""

import logging

from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.core.tasks import NO_RETRY, locked, periodic, task

from . import services

logger = logging.getLogger(__name__)


@periodic("47 5 * * *")
@locked()
@task("vendors.check_establishments", retry=NO_RETRY)
def check_establishments(timestamp: int) -> None:
    """Every night: flag active vendors established outside the allowed vendor countries.

    The set (`PlatformOperator.allowed_vendor_countries`) can shrink, and identity changes are
    flagged when saved; this re-check logs each active vendor outside the set as a warning so
    staff can decide (spec 11 CMP.13). Nobody is suspended.
    """
    count = services.report_vendors_outside_allowed()
    logger.info("vendors.check_establishments found %d vendor(s) outside the set", count)


@periodic("23 * * * *")
@locked()
@task("vendors.purge_unconfirmed_applications", retry=NO_RETRY)
def purge_unconfirmed_applications(timestamp: int) -> None:
    """Every hour: delete "Sell with us" applications nobody confirmed (VEN.22, D-24).

    The account, its applicant vendor and memberships go `VENDOR_APPLICATION_RETENTION_DAYS`
    days (3) after the request. Audited by count only.
    """
    changes = services.purge_unconfirmed_applications(
        ctx=AuditContext(
            actor_label="system:purge_unconfirmed_applications", source=AuditLog.Source.SYSTEM
        )
    )
    logger.info(
        "vendors.purge_unconfirmed_applications deleted %d application(s)", len(changes.changes)
    )


@periodic("41 * * * *")
@locked()
@task("vendors.remind_unconfirmed_applications", retry=NO_RETRY)
def remind_unconfirmed_applications(timestamp: int) -> None:
    """Every hour: the one reminder of an unconfirmed "Sell with us" application (D-24).

    Sent `VENDOR_APPLICATION_REMINDER_HOURS` hours (48) after the request, once: the marker and
    the outbox idempotency key make a re-run send nothing more.
    """
    changes = services.send_application_reminders(
        ctx=AuditContext(
            actor_label="system:remind_unconfirmed_applications", source=AuditLog.Source.SYSTEM
        )
    )
    logger.info("vendors.remind_unconfirmed_applications sent %d reminder(s)", len(changes.changes))
