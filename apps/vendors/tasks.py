"""Background jobs of the vendors app (ADR-0004, docs/dev/background-jobs.md)."""

import logging

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
