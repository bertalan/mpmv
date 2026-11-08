"""Background jobs of the tax app (ADR-0004, docs/dev/background-jobs.md)."""

import logging

from apps.core.tasks import NO_RETRY, Retry, locked, periodic, task

from . import services

logger = logging.getLogger(__name__)

#: VIES outages usually last minutes to hours: retry from 1 minute, doubling up to 1 hour.
VIES_RETRY = Retry(max_attempts=8, base_delay=60, max_delay=3600)


@task("tax.recheck_vat_number", retry=VIES_RETRY)
def recheck_vat_number(country: str, vat_number: str) -> None:
    """Ask VIES again about a VAT number and store the answer (TAX.5).

    The only place where a VIES check is retried: while VIES gives no answer the job raises
    `ViesUnavailable` and is retried with backoff. Checkout and other requests ask VIES once.
    A number VIES cannot be asked about is logged and dropped.
    """
    check = services.recheck_vat_number(country, vat_number)
    logger.info("tax.recheck_vat_number %s: %s", country, check.reason)


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
