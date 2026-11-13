"""The order side of a confirmed payment (PAY.2): `on_payment_succeeded` marks the order paid.

This app sits above `orders` in the layers, so it calls the order service; `orders` never sees
the signal. Runs in the webhook transaction, so a failure rolls the payment row back and the
provider's retry runs again.
"""

from typing import Any

from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.orders.services import mark_order_paid

from .models import Payment

WEBHOOK_ACTOR = "webhook:payments"


def order_paid_on_payment(sender: type[Payment], payment: Payment, **kwargs: Any) -> None:
    ctx = AuditContext(actor_label=WEBHOOK_ACTOR, source=AuditLog.Source.SYSTEM)
    mark_order_paid(
        payment.order_ref,
        amount=payment.amount,
        currency=payment.currency,
        provider_ref=payment.provider_ref,
        ctx=ctx,
    )
