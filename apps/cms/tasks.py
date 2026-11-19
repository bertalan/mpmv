"""Background jobs of the CMS (ADR-0004, docs/dev/background-jobs.md)."""

import logging

from django.core.management import call_command

from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.core.tasks import NO_RETRY, locked, periodic, task


@periodic("*/5 * * * *")
@locked()
@task("cms.publish_scheduled", retry=NO_RETRY)
def publish_scheduled(timestamp: int) -> None:
    """Every five minutes: publish the pages whose go-live has come and unpublish the expired
    ones (Wagtail's `publish_scheduled`), so a scheduled article (CMS.3) appears on time."""
    call_command("publish_scheduled")


logger = logging.getLogger(__name__)


@periodic("33 4 * * *")
@locked()
@task("cms.purge_form_submissions", retry=NO_RETRY)
def purge_form_submissions(timestamp: int) -> None:
    """Every night: delete the form page submissions kept past their page's retention (CMS.13).

    One audit entry per page, counts only; a second run finds nothing, so a replay is safe.
    """
    from . import services

    changes = services.purge_expired_form_submissions(
        ctx=AuditContext(actor_label="system:purge_form_submissions", source=AuditLog.Source.SYSTEM)
    )
    logger.info("cms.purge_form_submissions purged %d form page(s)", len(changes.changes))


@periodic("7 4 * * *")
@locked()
@task("cms.purge_page_revisions", retry=NO_RETRY)
def purge_page_revisions(timestamp: int) -> None:
    """Every night: delete the page revisions older than 12 months beyond each page's latest
    ten (CMS.18). One audit entry per run, counts only; a second run finds nothing."""
    from . import retention

    result = retention.purge_page_revisions(
        ctx=AuditContext(actor_label="system:purge_page_revisions", source=AuditLog.Source.SYSTEM),
        dry_run=False,
    )
    logger.info(
        "cms.purge_page_revisions deleted %d revision(s) of %d page(s)",
        result.deleted_total,
        len(result.deleted),
    )
