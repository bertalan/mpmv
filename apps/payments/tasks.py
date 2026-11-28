"""Background jobs of the payments app (ADR-0004, docs/dev/background-jobs.md)."""

import logging

from apps.core.tasks import NO_RETRY, locked, periodic, task

from . import duplicates, stray
from .models import Payment

logger = logging.getLogger(__name__)


@task("payments.refund_duplicate_payment", retry=NO_RETRY)
def refund_duplicate_payment(payment_id: int) -> None:
    """Queued in the webhook transaction that flagged the duplicate (PAY.4): refund it in full.
    A provider failure is audited and left to the sweep below."""
    payment = Payment.objects.filter(pk=payment_id).first()
    if payment is None:
        return
    changes = duplicates.refund_duplicate_payment(payment)
    logger.info(
        "payments.refund_duplicate_payment %s: %d change(s)", payment_id, len(changes.changes)
    )


@periodic("*/10 * * * *")
@locked()
@task("payments.refund_pending_duplicates", retry=NO_RETRY)
def refund_pending_duplicates(timestamp: int) -> None:
    """Every 10 minutes: refund the duplicate payments whose refund failed or was lost."""
    done = duplicates.refund_pending_duplicates()
    logger.info("payments.refund_pending_duplicates: %d refunded", done)


@periodic("13 3 * * *")  # 03:13 in the business time zone
@locked()
@task("payments.sweep_stray_captures", retry=NO_RETRY)
def sweep_stray_captures(timestamp: int) -> None:
    """Nightly (PAY.4): close the stray-payment alerts 14 days old with a full refund confirmed,
    remind support about the others once a day."""
    changes = stray.sweep_stray_captures()
    logger.info("payments.sweep_stray_captures: %d change(s)", len(changes.changes))
