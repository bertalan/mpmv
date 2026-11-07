"""Core services: the dry-run + audit pattern shared by all mutating services (spec 01
conventions), the platform operator and the worker heartbeat."""

import datetime as dt
from collections.abc import Callable
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from .audit import AuditContext, record
from .changeset import ChangeSet
from .models import Heartbeat, PlatformOperator
from .validity import NoValidRow, OverlappingValidity, pick_valid


def run_mutation(
    changeset: ChangeSet,
    apply: Callable[[], None],
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
        for change in changeset.changes:
            record(ctx, action, change.model, change.key, change.before, change.after)
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
    changeset.create(OPERATOR_MODEL, str(new.public_id), operator_snapshot(new))

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
