"""Webhook processing and payment recording (PAY.2), and the payment monitoring check (ORD.10).

Views are thin adapters over this. The ORD.10 check runs over a `PaymentSource`, the one query the
models must answer; `payment_source()` is the single place PAY.3 wires the real one in.
"""

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.alerts import raise_alert
from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.models import AuditLog
from apps.core.opschecks import Alert
from apps.core.services import run_mutation

from . import connect, registry, stripe_engine
from .changeset_keys import PAYMENT_MODEL, PAYMENT_SUCCEEDED
from .engine import InvalidSignature, ProviderError, WebhookEvent
from .models import Payment, ProcessedEvent
from .signals import on_payment_succeeded
from .transfer_alert import PaidPayment, payment_without_transfer_alert

WEBHOOK_ACTOR = "webhook:payments"
SUCCEEDED_EVENTS = frozenset({"payment_intent.succeeded", "payment.succeeded"})
ACCOUNT_UPDATED = "account.updated"  # a connected account's onboarding status (VEN.6)


class Outcome(StrEnum):
    PROCESSED = "processed"
    DUPLICATE = "duplicate"
    NOT_CONFIGURED = "not_configured"
    BAD_SIGNATURE = "bad_signature"
    MALFORMED = "malformed"


@dataclass(frozen=True)
class WebhookResult:
    outcome: Outcome
    event: WebhookEvent | None = None


def checkout_methods() -> Sequence[str]:
    """The methods checkout may offer: only what the configured provider reports (D-09)."""
    provider = registry.configured_provider()
    return tuple(provider.supported_methods()) if provider else ()


def process_webhook(
    body: bytes, signature: str, *, ip: str | None = None, dry_run: bool = False
) -> WebhookResult:
    """Verify, deduplicate on the event id, then record. A replay is a no-op.

    A dry run verifies and reports what would happen (PROCESSED or DUPLICATE) and stores nothing."""
    provider = registry.configured_provider()
    if provider is None:
        return WebhookResult(Outcome.NOT_CONFIGURED)
    try:
        event = provider.parse_webhook(body, signature)
    except (InvalidSignature, ProviderError):
        return WebhookResult(Outcome.BAD_SIGNATURE)
    if dry_run:
        seen = ProcessedEvent.objects.filter(provider=provider.code, event_id=event.event_id)
        return WebhookResult(Outcome.DUPLICATE if seen.exists() else Outcome.PROCESSED, event)
    ctx = AuditContext(actor_label=WEBHOOK_ACTOR, source=AuditLog.Source.SYSTEM, ip=ip)
    try:
        with transaction.atomic():
            ProcessedEvent.objects.create(
                provider=provider.code, event_id=event.event_id, event_type=event.type
            )
            if event.type in SUCCEEDED_EVENTS:
                record_payment_succeeded(provider.code, event, ctx=ctx)
            elif event.type == ACCOUNT_UPDATED:
                try:
                    connect.handle_account_updated(event.payload, ctx=ctx)
                except (KeyError, TypeError, ValueError) as error:
                    raise ProviderError("account event without an account id") from error
    except ProviderError:  # a signed event without the fields of a payment: nothing was stored
        return WebhookResult(Outcome.MALFORMED, event)
    except IntegrityError:
        if ProcessedEvent.objects.filter(provider=provider.code, event_id=event.event_id).exists():
            return WebhookResult(Outcome.DUPLICATE, event)
        raise
    return WebhookResult(Outcome.PROCESSED, event)


def record_payment_succeeded(
    provider_code: str, event: WebhookEvent, *, ctx: AuditContext
) -> Payment | None:
    """Store the confirmed payment, audit it and send `on_payment_succeeded`.

    Returns None when the payment is already recorded (a second event for the same payment)."""
    obj = event.payload.get("object", event.payload)
    try:
        provider_ref = str(obj["id"])
        currency = str(obj["currency"]).upper()
        amount = stripe_engine.from_minor(int(obj.get("amount_received", obj["amount"])), currency)
    except (KeyError, TypeError, ValueError) as error:
        raise ProviderError("payment event without id, amount or currency") from error
    if Payment.objects.filter(provider=provider_code, provider_ref=provider_ref).exists():
        return None
    payment = Payment(
        provider=provider_code,
        provider_ref=provider_ref,
        charge_ref=str(obj.get("latest_charge") or ""),
        order_ref=str((obj.get("metadata") or {}).get("order_ref", "")),
        amount=amount,
        currency=currency,
        succeeded_at=timezone.now(),
    )
    changeset = ChangeSet()
    changeset.create(
        PAYMENT_MODEL,
        provider_ref,
        {
            "provider": provider_code,
            "order_ref": payment.order_ref,
            "amount": amount,
            "currency": currency,
            "charge_ref": payment.charge_ref,
        },
    )
    run_mutation(changeset, payment.save, ctx=ctx, action=PAYMENT_SUCCEEDED, dry_run=False)
    on_payment_succeeded.send(sender=Payment, payment=payment)
    return payment


DEFAULT_GRACE_MINUTES = 60


class PaymentSource(Protocol):
    def succeeded_payments(self, *, since: dt.datetime) -> list[PaidPayment]:
        """Payments that succeeded at or after `since`, each flagged with whether a vendor
        transfer exists for it (refunded payments excluded by the implementation)."""
        ...


class NoPaymentSource:
    """Placeholder until PAY.3: there are no payments to check."""

    wired = False

    def succeeded_payments(self, *, since: dt.datetime) -> list[PaidPayment]:
        return []


def payment_source() -> PaymentSource:
    return NoPaymentSource()


def grace_period() -> dt.timedelta:
    return dt.timedelta(
        minutes=int(getattr(settings, "PAYMENT_TRANSFER_GRACE_MINUTES", DEFAULT_GRACE_MINUTES))
    )


def check_payments_without_transfer(
    source: PaymentSource | None = None, *, now: dt.datetime | None = None
) -> Alert | None:
    """Raise the alert (log, tracker, staff e-mail through OPS.2) when a succeeded payment has no
    transfer after the grace period; returns it, or None when all is well. Looks back
    `PAYMENT_TRANSFER_LOOKBACK_DAYS` (30) days."""
    moment = now or timezone.now()
    lookback = dt.timedelta(days=int(getattr(settings, "PAYMENT_TRANSFER_LOOKBACK_DAYS", 30)))
    chosen = source or payment_source()
    alert = payment_without_transfer_alert(
        chosen.succeeded_payments(since=moment - lookback), moment, grace=grace_period()
    )
    if alert is not None:
        raise_alert(alert, now=moment)
    return alert
