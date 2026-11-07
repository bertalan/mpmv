"""Background jobs of the tax app (ADR-0004, docs/dev/background-jobs.md)."""

import logging

from apps.core.tasks import NO_RETRY, locked, periodic, task

from . import services

logger = logging.getLogger(__name__)


@periodic("41 5 * * *")
@locked()
@task("tax.check_profile_consistency", retry=NO_RETRY)
def check_profile_consistency(timestamp: int) -> None:
    """Every night: flag current tax profiles that disagree with the vendor's fiscal regime.

    The fiscal regime (VEN.12) can change outside the regime change workflow (TAX.2); each
    mismatch is logged as a warning, and the tax admin shows the same list to staff.
    """
    count = services.report_inconsistent_tax_profiles()
    logger.info("tax.check_profile_consistency found %d inconsistent profile(s)", count)
