"""A stray capture: a payment on a cancelled order, or of another amount or currency (PAY.4).

Owner decision 2026-11-21: the platform does not refund it on its own. Staff get one alert per
payment and support refunds it by hand at the provider. Until then the money stays recorded as
received (the `Payment` row) and unallocated, as for a duplicate payment: no ledger line, no vendor
share, no transfer (`on_order_paid` is never sent for it), and the order state does not change.
The audit entry and the alert carry only references and amounts, never personal data.

Owner decisions 2026-11-21 (follow-up): a capture with a wrong order reference, or matching no
order, raises the same alert. Support closes an alert by hand with a reason; a nightly job closes
it after 14 days only once the provider confirms a full refund, and otherwise reminds support once
a day. The alert's state is its audit trail: flagged, reminded, closed (the log is append-only).
"""

import datetime as dt
import hashlib
import logging
from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone
from django.utils.translation import gettext

from apps.accounts.models import User
from apps.core.alerts import raise_alert
from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.models import AuditLog
from apps.core.opschecks import Alert
from apps.core.services import PLATFORM_OWNER_PERMISSION, audit_changes
from apps.core.tasks import business_tz

from . import engine, registry
from .changeset_keys import (
    PAYMENT_MODEL,
    PAYMENT_STRAY_CHECKED,
    PAYMENT_STRAY_CLOSED,
    PAYMENT_STRAY_FLAGGED,
    PAYMENT_STRAY_REMINDED,
)
from .models import Payment

logger = logging.getLogger(__name__)

ALERT_KEY = "payments:stray-capture"
SUMMARIES = {
    "late": "A payment succeeded on an order that is no longer awaiting payment. "
    "It has not been refunded: please refund it by hand.",
    "mismatch": "A payment succeeded with an amount or currency that does not match the order. "
    "It has not been refunded: please refund it by hand.",
    "wrong_reference": "A payment succeeded that is not the one started for the order. "
    "It has not been refunded: please refund it by hand.",
    "unmatched": "A payment succeeded that matches no order. "
    "It has not been refunded: please refund it by hand.",
}
REMINDER_KEY = "payments:stray-capture-reminder"
REMINDER = (
    "A stray payment flagged 14 or more days ago is still open and its full refund is not "
    "confirmed by the provider. Please refund it by hand, or close the alert with a reason."
)
SUPPORT_PERMISSION = "manager.platform_support"
SYSTEM_CTX = AuditContext(
    actor_label="job:payments.sweep_stray_captures", source=AuditLog.Source.SYSTEM
)


def flag_stray_capture(
    payment: Payment, *, reason: str, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Audit `payment` as a stray capture and alert staff, once per payment. No refund call; a
    dry run returns the change and writes nothing."""
    changes = ChangeSet()
    if AuditLog.objects.filter(
        target_model=PAYMENT_MODEL, action=PAYMENT_STRAY_FLAGGED, target_key=payment.provider_ref
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


def _closer(actor: User) -> str:
    return _staff_role(actor, gettext("Only Support or the platform owner may close this alert."))


def _staff_role(actor: User, refusal: str) -> str:
    """Who may close an alert by hand: Support or the platform owner (role matrix row
    `platform.support_actions`). No step-up: closing moves no money."""
    if actor.is_active and actor.has_perm(PLATFORM_OWNER_PERMISSION):
        return "platform_owner"
    if actor.is_active and actor.has_perm(SUPPORT_PERMISSION):
        return "support"
    raise PermissionDenied(refusal)


def _is_open(provider_ref: str) -> bool:
    flagged = AuditLog.objects.filter(
        target_model=PAYMENT_MODEL, action=PAYMENT_STRAY_FLAGGED, target_key=provider_ref
    )
    closed = AuditLog.objects.filter(
        target_model=PAYMENT_MODEL, action=PAYMENT_STRAY_CLOSED, target_key=provider_ref
    )
    return flagged.exists() and not closed.exists()


def _open_flags() -> QuerySet[AuditLog]:
    """The flag entries of the alerts still open, oldest first."""
    closed = AuditLog.objects.filter(
        target_model=PAYMENT_MODEL, action=PAYMENT_STRAY_CLOSED
    ).values("target_key")
    return (
        AuditLog.objects.filter(target_model=PAYMENT_MODEL, action=PAYMENT_STRAY_FLAGGED)
        .exclude(target_key__in=closed)
        .order_by("created_at", "id")
    )


def _open_with_flags() -> list[tuple[Payment, AuditLog]]:
    """Each open alert's payment with its flag entry, oldest flag first: two queries."""
    flags = list(_open_flags().only("target_key", "after", "created_at"))
    refs = [flag.target_key for flag in flags]
    by_ref = {p.provider_ref: p for p in Payment.objects.filter(provider_ref__in=refs)}
    return [(by_ref[f.target_key], f) for f in flags if f.target_key in by_ref]


def open_stray_captures() -> list[Payment]:
    """The payments whose stray-capture alert is open, oldest flag first."""
    return [payment for payment, _flag in _open_with_flags()]


@dataclass(frozen=True)
class OpenAlert:
    """One open stray-payment alert as the Manager lists it: references and amounts only."""

    payment_ref: str
    order_ref: str
    reason: str
    amount: Decimal
    currency: str
    age_days: int
    checked_at: dt.datetime | None  # None: no refund check recorded yet
    refund_confirmed: bool | None  # None: not checked, or the provider could not tell


def _recorded_check(after: object) -> tuple[dt.datetime, Decimal | None] | None:
    """The refund check a reminder or a "Check now" entry carries, if any."""
    if not isinstance(after, dict) or not after.get("checked_at"):
        return None
    value = after.get("refunded")
    when = dt.datetime.fromisoformat(str(after["checked_at"]))
    return when, None if value is None else Decimal(str(value))


def open_alerts(*, now: dt.datetime | None = None) -> list[OpenAlert]:
    """The open alerts, oldest first, for the Manager page. Three queries whatever the rows and
    no provider call: the refund state is the last one recorded, by the nightly job's reminder or
    by a "Check now"."""
    moment = now or timezone.now()
    pairs = _open_with_flags()
    checks: dict[str, tuple[dt.datetime, Decimal | None]] = {}
    for entry in (
        AuditLog.objects.filter(
            target_model=PAYMENT_MODEL,
            action__in=(PAYMENT_STRAY_REMINDED, PAYMENT_STRAY_CHECKED),
            target_key__in=[payment.provider_ref for payment, _flag in pairs],
        )
        .order_by("created_at", "id")
        .only("target_key", "after")
    ):
        check = _recorded_check(entry.after)
        if check is not None:
            checks[entry.target_key] = check  # the latest wins
    rows = []
    for payment, flag in pairs:
        checked_at, refunded = checks.get(payment.provider_ref, (None, None))
        rows.append(
            OpenAlert(
                payment_ref=payment.provider_ref,
                order_ref=payment.order_ref,
                reason=str((flag.after or {}).get("reason", "mismatch")),
                amount=payment.amount,
                currency=payment.currency,
                age_days=(moment - flag.created_at).days,
                checked_at=checked_at,
                refund_confirmed=None if refunded is None else refunded >= payment.amount,
            )
        )
    return rows


def check_refund_now(
    provider_ref: str, *, actor: User, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Support asks the provider once for the refund state of one open alert and records it
    (`stray_checked`). An alert closed, or never raised, gives an empty ChangeSet and no call."""
    by = _staff_role(actor, gettext("Only Support or the platform owner may check this refund."))
    with transaction.atomic():
        payment = _lock_open(provider_ref)
    if payment is None:
        return ChangeSet()
    # The provider call runs outside the lock (no network wait while the row is held); the state
    # is read again under the lock before the check is recorded, so a close in between wins.
    refunded = _refunded(payment)
    changes = ChangeSet()
    with transaction.atomic():
        if _lock_open(provider_ref) is None:
            return changes
        detail = {"by": by, **_check_detail(refunded, timezone.now())}
        changes.create(PAYMENT_MODEL, provider_ref, detail)
        if not dry_run:
            audit_changes(changes, ctx=ctx, action=PAYMENT_STRAY_CHECKED)
    return changes


def _lock_open(provider_ref: str) -> Payment | None:
    """Lock the payment row (inside a transaction) and return it while its alert is open.

    The payment row is the alert's lock: the manual close, "Check now" and the nightly auto-close
    all take it, then read the alert state again, so two of them never both act on one alert."""
    payment = Payment.objects.select_for_update().filter(provider_ref=provider_ref).first()
    if payment is None or not _is_open(provider_ref):
        return None
    return payment


def _check_detail(refunded: Decimal | None, at: dt.datetime) -> dict[str, object]:
    """The refund check as an audit entry keeps it: the amount (or null) and when."""
    return {"refunded": None if refunded is None else str(refunded), "checked_at": at.isoformat()}


def _close(
    payment: Payment, detail: dict[str, object], ctx: AuditContext, dry_run: bool
) -> ChangeSet:
    changes = ChangeSet()
    changes.update(
        PAYMENT_MODEL, payment.provider_ref, {"alert": "open"}, {"alert": "closed", **detail}
    )
    if not dry_run:
        audit_changes(changes, ctx=ctx, action=PAYMENT_STRAY_CLOSED)
    return changes


def close_stray_capture(
    provider_ref: str,
    *,
    actor: User,
    reason: str,
    ctx: AuditContext,
    dry_run: bool = False,
) -> ChangeSet:
    """Support closes the stray-capture alert of `provider_ref` by hand, with a reason. The audit
    keeps the role and the reason's length and SHA-256 only (it may name the customer). An alert
    already closed, or never raised, gives an empty ChangeSet."""
    by = _closer(actor)
    text = " ".join(str(reason or "").split())
    if not text:
        raise ValidationError(gettext("Please give the reason for closing this alert."))
    detail: dict[str, object] = {
        "by": by,
        "reason_length": len(text),
        "reason_sha256": hashlib.sha256(text.encode()).hexdigest(),
    }
    with transaction.atomic():
        # Two closes at once write one entry: the second sees the first under the lock.
        payment = _lock_open(provider_ref)
        if payment is None:
            return ChangeSet()
        return _close(payment, detail, ctx, dry_run)


def _refunded(payment: Payment) -> Decimal | None:
    """What the provider says was refunded on the payment, or None when it cannot be read."""
    try:
        reader = getattr(registry.get_provider(payment.provider), "refunded_amount", None)
        if reader is None:
            return None
        value = reader(
            provider_ref=payment.provider_ref,
            charge_ref=payment.charge_ref,
            currency=payment.currency,
        )
    except (engine.ProviderError, registry.UnknownProvider) as error:
        logger.warning("refund state unreadable for %s: %s", payment.provider_ref, error)
        return None
    return Decimal(value)


def sweep_stray_captures(*, now: dt.datetime | None = None, dry_run: bool = False) -> ChangeSet:
    """The nightly job: close each open alert 14 days old whose full refund the provider confirms;
    remind support about the others of that age, once per alert and day."""
    moment = now or timezone.now()
    today = timezone.localtime(moment, business_tz()).date().isoformat()
    changes = ChangeSet()
    for payment in open_stray_captures():
        entry = AuditLog.objects.filter(
            target_model=PAYMENT_MODEL,
            action=PAYMENT_STRAY_FLAGGED,
            target_key=payment.provider_ref,
        ).earliest("created_at")
        reminded = AuditLog.objects.filter(
            target_model=PAYMENT_MODEL,
            action=PAYMENT_STRAY_REMINDED,
            target_key=payment.provider_ref,
            after__date=today,
        ).exists()
        age_ok = (moment - entry.created_at).days >= engine.STRAY_AUTO_CLOSE_DAYS
        refunded = _refunded(payment) if age_ok else None
        step = engine.stray_alert_step(
            flagged_at=entry.created_at,
            now=moment,
            amount=payment.amount,
            refunded=refunded,
            reminded_today=reminded,
        )
        if step == engine.STRAY_WAIT:
            continue
        with transaction.atomic():  # a manual close since the list was read wins
            if _lock_open(payment.provider_ref) is None:
                continue
            if step == engine.STRAY_CLOSE:
                detail = {"by": "auto", **_check_detail(refunded, moment)}
                done = _close(payment, detail, SYSTEM_CTX, dry_run)
                changes.changes.extend(done.changes)
            elif step == engine.STRAY_REMIND:
                changes.create(PAYMENT_MODEL, payment.provider_ref, {"date": today})
                if not dry_run:
                    _remind(payment, today, _check_detail(refunded, moment))
    return changes


def _remind(payment: Payment, today: str, check: dict[str, object]) -> None:
    """The daily reminder; its entry also records the refund check the job just ran."""
    reminder = ChangeSet()
    reminder.create(PAYMENT_MODEL, payment.provider_ref, {"date": today, **check})
    audit_changes(reminder, ctx=SYSTEM_CTX, action=PAYMENT_STRAY_REMINDED)
    raise_alert(
        Alert(
            REMINDER_KEY,
            REMINDER,
            {
                "order": payment.order_ref,
                "payment": payment.provider_ref,
                "amount": str(payment.amount),
                "currency": payment.currency,
            },
        ),
        dedup=f":{payment.provider_ref}:{today}",
    )
