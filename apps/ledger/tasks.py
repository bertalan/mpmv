"""Background jobs of the ledger app (ADR-0004, docs/dev/background-jobs.md)."""

import logging

from apps.core.tasks import NO_RETRY, locked, periodic, task
from apps.payments.models import Payment

from . import settlement

logger = logging.getLogger(__name__)


@task("ledger.transfer_payment", retry=NO_RETRY)
def transfer_payment(payment_id: int) -> None:
    """Queued in the transaction that marks an order paid (PAY.3): send each vendor's transfer.

    A provider failure is audited and left to the sweep below; nothing here can unpay the order."""
    payment = Payment.objects.filter(pk=payment_id).first()
    if payment is None:
        return
    outcome = settlement.transfer_payment(payment)
    logger.info("ledger.transfer_payment %s: %s", payment_id, outcome)


@periodic("*/10 * * * *")
@locked()
@task("ledger.settle_pending_payments", retry=NO_RETRY)
def settle_pending_payments(timestamp: int) -> None:
    """Every 10 minutes: post and transfer whatever a paid order still lacks (failed transfers,
    a vendor whose account became ready, a posting that was blocked and fixed). Idempotent."""
    outcome = settlement.settle_pending()
    logger.info("ledger.settle_pending_payments: %s", outcome)
