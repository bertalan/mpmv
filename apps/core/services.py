"""Core services: the dry-run + audit pattern shared by all mutating services (spec 01
conventions), the platform operator, the worker heartbeat and throttle counters."""

import datetime as dt
from collections.abc import Callable
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Model, Q
from django.utils import timezone

from .audit import AuditContext, record
from .changeset import ChangeSet
from .models import Heartbeat, PlatformOperator, Throttle
from .throttling import BackoffPolicy, ThrottleState, after_failure
from .validity import NoValidRow, OverlappingValidity, pick_valid


def run_mutation(
    changeset: ChangeSet,
    apply: Callable[[], object],
    *,
    ctx: AuditContext,
    action: str,
    dry_run: bool,
) -> ChangeSet:
    """Return the ChangeSet; unless dry-run, apply it and audit every change atomically."""
    if dry_run or changeset.is_empty:
        return changeset
    with transaction.atomic():
        apply()
        audit_changes(changeset, ctx=ctx, action=action)
    return changeset


def audit_changes(changeset: ChangeSet, *, ctx: AuditContext, action: str) -> None:
    """One AuditLog entry per change. For changes already applied by the caller in the same
    transaction (e.g. many-to-many fields saved by a form); otherwise use `run_mutation`."""
    for change in changeset.changes:
        record(ctx, action, change.model, change.key, change.before, change.after)


def save_audited[M: Model](
    instance: M,
    snapshot: Callable[[M], dict[str, Any]],
    *,
    key: str,
    ctx: AuditContext,
    created: str,
    updated: str,
    dry_run: bool = False,
) -> ChangeSet:
    """Insert or update `instance`, auditing the fields `snapshot` exposes (SEC.4).

    For an existing row, `before` is read from the database (row locked), never from the
    edited instance. The row is saved even when no audited field changed, but only real
    changes are audited.
    """
    label = instance._meta.label_lower
    adding = instance._state.adding
    changeset = ChangeSet()
    with transaction.atomic():
        if adding:
            changeset.create(label, key, snapshot(instance))
        else:
            stored = type(instance)._default_manager.select_for_update().get(pk=instance.pk)
            changeset.update(label, key, snapshot(stored), snapshot(instance))
        if dry_run:
            return changeset
        instance.save()
        audit_changes(changeset, ctx=ctx, action=created if adding else updated)
    return changeset


# --- Platform operator (ARC.7)


class NoPlatformOperator(NoValidRow):
    """No PlatformOperator is configured for the requested instant."""


class OverlappingPlatformOperators(OverlappingValidity):
    """More than one PlatformOperator covers the requested instant."""


OPERATOR_MODEL = "core.platformoperator"
OPERATOR_CONFIGURED = "core.platform_operator.configured"
OPERATOR_FIELDS = (
    "legal_name",
    "country",
    "vat_number",
    "tax_regime",
    "commission_vat_mode",
    "einvoice_channel",
    "dac7_authority_country",
    "valid_from",
    "valid_to",
)


def operator_snapshot(op: PlatformOperator) -> dict[str, Any]:
    return {name: getattr(op, name) for name in OPERATOR_FIELDS}


def current_operator(at: dt.datetime | None = None) -> PlatformOperator:
    """The PlatformOperator valid at `at` (default: now); a clear error if none or ambiguous."""
    at = at or timezone.now()
    rows = PlatformOperator.objects.filter(valid_from__lte=at).filter(
        Q(valid_to__isnull=True) | Q(valid_to__gt=at)
    )
    try:
        return pick_valid(rows, at, what="PlatformOperator")
    except OverlappingValidity as exc:
        raise OverlappingPlatformOperators(str(exc)) from exc
    except NoValidRow as exc:
        raise NoPlatformOperator(str(exc)) from exc


def latest_operator() -> PlatformOperator | None:
    return PlatformOperator.objects.order_by("-valid_from").first()


def check_operator_start(valid_from: dt.datetime, latest: PlatformOperator | None) -> None:
    """A new configuration starts after the latest one, and only an open-ended row is closed."""
    if latest is None:
        return
    if valid_from <= latest.valid_from:
        start = latest.valid_from.isoformat()
        raise ValidationError({"valid_from": f"Must be after {start}, the latest start."})
    if latest.valid_to is not None and latest.valid_to > valid_from:
        end = latest.valid_to.isoformat()
        raise ValidationError({"valid_from": f"Must not be before the latest end, {end}."})


def configure_platform_operator(
    new: PlatformOperator, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Add a configuration row from `new.valid_from`, closing the open-ended current one.

    Existing rows are never edited, so documents and snapshots computed under an earlier
    configuration remain explainable.
    """
    if new.pk is not None:
        raise ValueError("Pass a new, unsaved PlatformOperator")
    new.valid_to = None
    new.full_clean(validate_constraints=False)  # constraints are enforced by the database
    latest = latest_operator()
    check_operator_start(new.valid_from, latest)

    changeset = ChangeSet()
    closing = latest if latest is not None and latest.valid_to is None else None
    if closing is not None:
        changeset.update(
            OPERATOR_MODEL,
            str(closing.public_id),
            {"valid_to": None},
            {"valid_to": new.valid_from},
        )
    changeset.create(
        OPERATOR_MODEL,
        str(new.public_id),
        operator_snapshot(new),
        supersedes=operator_snapshot(latest) if latest is not None else None,
    )

    def apply() -> None:
        if closing is not None:
            closing.valid_to = new.valid_from
            closing.save(update_fields=["valid_to", "updated_at"])
        new.save()

    return run_mutation(changeset, apply, ctx=ctx, action=OPERATOR_CONFIGURED, dry_run=dry_run)


# --- Worker heartbeat (ARC.9)


def record_heartbeat(name: str, *, scheduled_for: dt.datetime | None = None) -> Heartbeat:
    """Count one beat for `name` and remember when it happened.

    Operational telemetry, not a domain change: no ChangeSet, dry-run or audit entry.
    """
    now = timezone.now()
    beat, _ = Heartbeat.objects.update_or_create(
        name=name,
        defaults={"beats": F("beats") + 1, "last_beat_at": now, "scheduled_for": scheduled_for},
        create_defaults={"beats": 1, "last_beat_at": now, "scheduled_for": scheduled_for},
    )
    beat.refresh_from_db()
    return beat


# --- Throttling (CORE.5)
# Operational counters, not domain changes: no ChangeSet or dry-run. Callers audit the lockouts
# they care about (see apps.accounts.services).


def _state_of(row: Throttle | None, now: dt.datetime) -> ThrottleState:
    if row is None:
        return ThrottleState()
    state = ThrottleState(
        failures=row.failures, locked_until=row.locked_until, expires_at=row.expires_at
    )
    return ThrottleState() if state.is_expired(now) else state


def throttle_state(key: str, *, now: dt.datetime | None = None) -> ThrottleState:
    """The current state of `key`; a clean state when the key is unknown or expired."""
    return _state_of(Throttle.objects.filter(key=key).first(), now or timezone.now())


def record_throttle_failure(
    key: str, policy: BackoffPolicy, *, now: dt.datetime | None = None
) -> tuple[ThrottleState, ThrottleState]:
    """Count one failure for `key` atomically; return the state before and after it."""
    now = now or timezone.now()
    with transaction.atomic():
        row, _ = Throttle.objects.select_for_update().get_or_create(
            key=key, defaults={"expires_at": now}
        )
        before = _state_of(row, now)
        after = after_failure(before, now, policy)
        row.failures = after.failures
        row.locked_until = after.locked_until
        row.expires_at = after.expires_at or now
        row.save(update_fields=["failures", "locked_until", "expires_at", "updated_at"])
    return before, after


def clear_throttle(key: str) -> None:
    """Forget every failure of `key` (e.g. after a successful login)."""
    Throttle.objects.filter(key=key).delete()


def purge_expired_throttles(*, now: dt.datetime | None = None) -> int:
    """Delete the rows nobody needs any more; return how many were deleted."""
    deleted, _ = Throttle.objects.filter(expires_at__lte=now or timezone.now()).delete()
    return deleted
