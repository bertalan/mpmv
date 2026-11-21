"""A stray capture: a payment on a cancelled order, or of another amount or currency (PAY.4).

Owner decision 2026-11-21: the platform does not refund it on its own. Staff get one alert per
payment and support refunds it by hand at the provider. Until then the money stays recorded as
received (the `Payment` row) and unallocated, as for a duplicate payment: no ledger line, no vendor
share, no transfer (`on_order_paid` is never sent for it), and the order state does not change.
The audit entry and the alert carry only references and amounts, never personal data.
"""

from apps.core.alerts import raise_alert
from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.models import AuditLog
from apps.core.opschecks import Alert
from apps.core.services import audit_changes

from .changeset_keys import PAYMENT_MODEL, PAYMENT_STRAY_FLAGGED
from .models import Payment

ALERT_KEY = "payments:stray-capture"
SUMMARIES = {
    "late": "A payment succeeded on an order that is no longer awaiting payment. "
    "It has not been refunded: please refund it by hand.",
    "mismatch": "A payment succeeded with an amount or currency that does not match the order. "
    "It has not been refunded: please refund it by hand.",
}


def flag_stray_capture(
    payment: Payment, *, reason: str, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Audit `payment` as a stray capture and alert staff, once per payment. No refund call; a
    dry run returns the change and writes nothing."""
    changes = ChangeSet()
    if AuditLog.objects.filter(
        action=PAYMENT_STRAY_FLAGGED, target_key=payment.provider_ref
    ).exists():
        return changes
    changes.create(
        PAYMENT_MODEL,
        payment.provider_ref,
        {
            "order_ref": payment.order_ref,
            "reason": reason,
            "amount": payment.amount,
            "currency": payment.currency,
        },
    )
    if dry_run:
        return changes
    audit_changes(changes, ctx=ctx, action=PAYMENT_STRAY_FLAGGED)
    raise_alert(
        Alert(
            ALERT_KEY,
            SUMMARIES.get(reason, SUMMARIES["mismatch"]),
            {
                "order": payment.order_ref,
                "payment": payment.provider_ref,
                "amount": str(payment.amount),
                "currency": payment.currency,
                "reason": reason,
            },
        ),
        dedup=f":{payment.provider_ref}",
    )
    return changes
