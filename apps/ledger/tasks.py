"""Background jobs of the ledger app (ADR-0004, docs/dev/background-jobs.md)."""

import logging

from apps.core.tasks import NO_RETRY, locked, periodic, task
from apps.payments.models import Payment

from . import refunds, settlement

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


@periodic("*/10 * * * *")
@locked()
@task("ledger.retry_pending_refund_reversals", retry=NO_RETRY)
def retry_pending_refund_reversals(timestamp: int) -> None:
    """Every 10 minutes: submit each amendment refund the provider has not taken yet (ORD.20),
    then retry the transfer reversal of each refund the provider already paid out to the buyer
    (PAY.4), both under the same idempotency key. Idempotent."""
    submitted = refunds.submit_pending_refunds()
    logger.info("ledger.retry_pending_refund_reversals: %s submitted", submitted)
    cleared = refunds.retry_pending_reversals()
    logger.info("ledger.retry_pending_refund_reversals: %s cleared", cleared)


@periodic("0 * * * *")
@locked()
@task("ledger.alert_refunds_near_due", retry=NO_RETRY)
def alert_refunds_near_due(timestamp: int) -> None:
    """Every hour: alert staff for each amendment refund the provider has not confirmed 4 days
    before its 14-day `due_by` (ORD.20; art. 61 c. 4 Codice del consumo). The alert email goes
    once per refund and repeat window."""
    count = refunds.alert_refunds_near_due()
    logger.info("ledger.alert_refunds_near_due: %d alerted", count)
