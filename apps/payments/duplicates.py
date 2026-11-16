"""A second payment on an order that is already paid (PAY.4, D-05/L-10, #438 review).

The order has one payment (`Order.payment_intent_ref`). When another PaymentIntent succeeds on it,
the customer's money is taken with no order behind it: a platform-error case. This module flags
the payment (inside the webhook transaction: audited `orders.payment_duplicate` by the orders
service, here the payment row and a staff alert) and refunds it in full through the provider,
outside that transaction (a queued job and a sweep, like the transfers).

What it never does: no transfer to any vendor (settlement skips a duplicate payment), no vendor
share reversed (the vendors were paid from the first payment, also when that transfer is still
pending), no refund fee (the platform made the error, L-10) and no ledger line. The provider cost
of this refund stays with the platform.
"""

import logging

from django.db import transaction

from apps.core.alerts import raise_alert
from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.models import AuditLog
from apps.core.opschecks import Alert
from apps.core.services import audit_changes

from . import registry
from .changeset_keys import (
    PAYMENT_DUPLICATE_FLAGGED,
    PAYMENT_DUPLICATE_REFUND_FAILED,
    PAYMENT_DUPLICATE_REFUNDED,
    PAYMENT_MODEL,
)
from .engine import ProviderError, RefundRequest
from .models import Payment

logger = logging.getLogger(__name__)

SWEEP_ACTOR = "system:duplicate-payment-refund"
ALERT_KEY = "payments:duplicate"


def system_ctx() -> AuditContext:
    return AuditContext(actor_label=SWEEP_ACTOR, source=AuditLog.Source.SYSTEM)


def flag_duplicate(payment: Payment, *, ctx: AuditContext) -> ChangeSet:
    """Mark `payment` as a duplicate and alert staff. Idempotent. Call it in the transaction that
    stored the payment; the refund itself is queued by the caller."""
    changes = ChangeSet()
    if payment.duplicate:
        return changes
    payment.duplicate = True
    payment.save(update_fields=["duplicate", "updated_at"])
    changes.update(
        PAYMENT_MODEL,
        payment.provider_ref,
        {"duplicate": False},
        {"duplicate": True, "order_ref": payment.order_ref, "amount": payment.amount},
    )
    audit_changes(changes, ctx=ctx, action=PAYMENT_DUPLICATE_FLAGGED)
    raise_alert(
        Alert(
            ALERT_KEY,
            "A second payment succeeded on an order that is already paid; it is being refunded",
            {
                "order": payment.order_ref,
                "payment": payment.provider_ref,
                "amount": str(payment.amount),
                "currency": payment.currency,
            },
        ),
        dedup=f":{payment.provider_ref}",
    )
    return changes


def refund_duplicate_payment(
    payment: Payment, *, ctx: AuditContext | None = None, dry_run: bool = False
) -> ChangeSet:
    """Refund a flagged duplicate payment in full, once. A dry run only reports the change.

    The provider key is stable per payment, so a retry after a failure or a lost answer never
    refunds twice. A provider failure is audited (once per payment and reason) and left to the
    sweep. Nothing else moves: see the module docstring."""
    ctx = ctx or system_ctx()
    changes = ChangeSet()
    with transaction.atomic():
        locked = Payment.objects.select_for_update().get(pk=payment.pk)
        if not locked.duplicate or locked.duplicate_refund_ref:
            return changes
        changes.update(
            PAYMENT_MODEL,
            locked.provider_ref,
            {"duplicate_refund_ref": ""},
            {"duplicate_refund_ref": "(set when the provider refunds)", "amount": locked.amount},
        )
        if dry_run:
            return changes
        try:
            result = registry.get_provider(locked.provider).refund(
                RefundRequest(
                    provider_ref=locked.provider_ref,
                    amount=locked.amount,
                    currency=locked.currency,
                    idempotency_key=f"duplicate-refund:{locked.provider_ref}",
                    reverse_vendor_shares=False,
                    reversals=(),
                )
            )
        except (ProviderError, registry.UnknownProvider) as error:
            _failed_once(locked, str(error), ctx)
            logger.warning("duplicate refund failed for %s: %s", locked.provider_ref, error)
            return ChangeSet()
        locked.duplicate_refund_ref = result.refund_ref
        locked.save(update_fields=["duplicate_refund_ref", "updated_at"])
        done = ChangeSet()
        done.update(
            PAYMENT_MODEL,
            locked.provider_ref,
            {"duplicate_refund_ref": ""},
            {
                "duplicate_refund_ref": result.refund_ref,
                "amount": result.amount,
                "provider_cost": result.provider_cost,
            },
        )
        audit_changes(done, ctx=ctx, action=PAYMENT_DUPLICATE_REFUNDED)
        return done


def _failed_once(payment: Payment, reason: str, ctx: AuditContext) -> None:
    known = AuditLog.objects.filter(
        action=PAYMENT_DUPLICATE_REFUND_FAILED,
        target_key=payment.provider_ref,
        after__reason=reason,
    )
    if known.exists():
        return
    changes = ChangeSet()
    changes.create(PAYMENT_MODEL, payment.provider_ref, {"reason": reason})
    audit_changes(changes, ctx=ctx, action=PAYMENT_DUPLICATE_REFUND_FAILED)


def refund_pending_duplicates() -> int:
    """The sweep: refund every flagged duplicate that has no refund yet. Returns how many refunds
    were made now."""
    done = 0
    pending = Payment.objects.filter(duplicate=True, duplicate_refund_ref="").order_by("id")
    for payment in pending:
        if not refund_duplicate_payment(payment).is_empty:
            done += 1
    return done
