"""The ledger's side of a paid order (PAY.3): receives `on_order_paid` from the payments app.

Runs in the webhook transaction: the vendor lines are written with the order's `paid` state, or
neither is. The transfers are queued in the same transaction (a job row, so a rollback drops it
and a commit cannot lose it) and sent outside it: a provider failure never touches the order or
the ledger lines.
"""

from typing import Any

from apps.core import tasks
from apps.payments.models import Payment

from . import settlement
from .tasks import transfer_payment


def post_and_queue_transfers(sender: type[Payment], payment: Payment, **kwargs: Any) -> None:
    settlement.post_payment(payment)
    tasks.enqueue(transfer_payment, payment_id=payment.pk)
