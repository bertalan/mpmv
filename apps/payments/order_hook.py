"""The order side of a confirmed payment (PAY.2): `on_payment_succeeded` marks the order paid.

This app sits above `orders` in the layers, so it calls the order service; `orders` never sees
the signal. Runs in the webhook transaction, so a failure rolls the payment row back and the
provider's retry runs again.
"""

from typing import Any

from apps.core import tasks
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.orders.services import flag_duplicate_payment, mark_order_paid

from . import duplicates
from .models import Payment
from .signals import on_order_paid
from .tasks import refund_duplicate_payment

WEBHOOK_ACTOR = "webhook:payments"


def order_paid_on_payment(sender: type[Payment], payment: Payment, **kwargs: Any) -> None:
    ctx = AuditContext(actor_label=WEBHOOK_ACTOR, source=AuditLog.Source.SYSTEM)
    paid = mark_order_paid(
        payment.order_ref,
        amount=payment.amount,
        currency=payment.currency,
        provider_ref=payment.provider_ref,
        ctx=ctx,
    )
    if paid:
        on_order_paid.send(sender=Payment, payment=payment)
        return
    # PAY.4: a payment on an order already paid with another one is money with no order behind
    # it. It is flagged (audited, staff alerted) and refunded in full by a queued job; no
    # `on_order_paid`, so no ledger line and no transfer comes from it.
    if flag_duplicate_payment(
        payment.order_ref, provider_ref=payment.provider_ref, amount=payment.amount, ctx=ctx
    ):
        duplicates.flag_duplicate(payment, ctx=ctx)
        tasks.enqueue(refund_duplicate_payment, payment_id=payment.pk)
