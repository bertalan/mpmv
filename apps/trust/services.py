"""Trust rules and snapshots (TRU.1): staff-edited, versioned, audited rules and the append-only
snapshot of a computed result. Building the inputs from orders and the refresh task are TRU.2.
"""

import datetime as dt
from collections.abc import Iterable, Mapping
from typing import Any

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.services import run_mutation
from apps.core.validity import NoValidRow, next_start_error, pick_valid
from apps.vendors.models import Vendor

from . import engine
from .models import TrustRules, VendorTrustSnapshot

TRUST_RULES_MODEL = "trust.trustrules"
SNAPSHOT_MODEL = "trust.vendortrustsnapshot"
RULES_CHANGED = "trust.rules_changed"
BACKDATE_TOLERANCE = dt.timedelta(minutes=1)  # a caller's "now" computed a moment earlier

__all__ = [
    "RULES_CHANGED",
    "NoValidRow",
    "add_trust_rules",
    "ensure_default_rules",
    "latest_snapshot",
    "record_snapshot",
    "rules_data",
    "rules_in_force",
]


def rules_snapshot(rules: TrustRules) -> dict[str, Any]:
    return {
        "version": rules.version,
        "params": rules.params,
        "note": rules.note,
        "valid_from": rules.valid_from,
        "valid_to": rules.valid_to,
    }


def rules_data(rules: TrustRules) -> engine.TrustRulesData:
    """The thresholds the engine takes."""
    return rules.to_data()


def rules_in_force(*, at: dt.datetime | None = None) -> TrustRules:
    """The rules valid at `at` (now by default). Raises `NoValidRow`: never a silent default."""
    return pick_valid(TrustRules.objects.all(), at or timezone.now(), what="trust rules")


def add_trust_rules(
    params: Mapping[str, Any],
    *,
    valid_from: dt.datetime,
    ctx: AuditContext,
    note: str = "",
    dry_run: bool = False,
    impact_inputs: Iterable[engine.VendorTrustInputs] | None = None,
) -> ChangeSet:
    """Add a rules version from `valid_from`, closing the open one; the version counts up.

    Existing rows are never edited: a snapshot keeps the version it was computed with. Raises
    `ValidationError` for unknown or invalid thresholds, or a start in the past or not after the
    latest version. With `impact_inputs` (one `VendorTrustInputs` per vendor, built by TRU.2),
    the new row's change carries `vendors_checked` and `vendors_changed`: how many vendors'
    badges or tiers would differ under the new thresholds, so staff see the effect in the dry
    run before they confirm.
    """
    if valid_from < timezone.now() - BACKDATE_TOLERANCE:
        raise ValidationError({"valid_from": "Trust rules cannot start in the past."})
    note = note.strip()
    with transaction.atomic():
        latest = TrustRules.objects.select_for_update().order_by("-version").first()
        version = 1 if latest is None else latest.version + 1
        try:
            new_data = engine.rules_from_params(version, params)
        except ValueError as exc:
            raise ValidationError({"params": str(exc)}) from None
        errors = engine.rules_errors(new_data)
        if errors:
            raise ValidationError({"params": [f"{k}: {v}" for k, v in errors.items()]})
        error = next_start_error(valid_from, latest)
        if error is not None:
            raise ValidationError({"valid_from": error})
        new = TrustRules(
            version=version,
            params=engine.rules_to_params(new_data),
            note=note,
            valid_from=valid_from,
        )
        changeset = ChangeSet()
        closing = latest if latest is not None and latest.valid_to is None else None
        if closing is not None:
            before = rules_snapshot(closing)
            changeset.update(
                TRUST_RULES_MODEL,
                str(closing.public_id),
                before,
                {**before, "valid_to": valid_from},
            )
        after = rules_snapshot(new)
        if impact_inputs is not None:
            after.update(_impact(impact_inputs, latest, new_data))
        changeset.create(
            TRUST_RULES_MODEL,
            str(new.public_id),
            after,
            supersedes=rules_snapshot(latest) if latest is not None else None,
        )

        def apply() -> None:
            try:
                with transaction.atomic():
                    if closing is not None:
                        closing.valid_to = valid_from
                        closing.save(update_fields=["valid_to", "updated_at"])
                    new.save()
            except IntegrityError:
                raise ValidationError(
                    {"valid_from": "Other trust rules were added at the same time."}
                ) from None

        return run_mutation(changeset, apply, ctx=ctx, action=RULES_CHANGED, dry_run=dry_run)


def _impact(
    inputs: Iterable[engine.VendorTrustInputs],
    current: TrustRules | None,
    new: engine.TrustRulesData,
) -> dict[str, int]:
    checked = changed = 0
    old = current.to_data() if current is not None else None
    for data in inputs:
        checked += 1
        after = engine.compute_vendor_trust(data, new)
        if old is None:
            changed += any(b.earned for b in after.badges)
            continue
        before = engine.compute_vendor_trust(data, old)
        changed += _outcome(before) != _outcome(after)
    return {"vendors_checked": checked, "vendors_changed": changed}


def _outcome(result: engine.VendorTrustResult) -> list[tuple[int, bool, int | None]]:
    return [(b.number, b.earned, b.tier) for b in result.badges]


def ensure_default_rules(*, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Create version 1 with the spec defaults when no version exists yet (setup, seed)."""
    if TrustRules.objects.exists():
        return ChangeSet()
    return add_trust_rules(
        engine.rules_to_params(engine.TrustRulesData(version=1)),
        valid_from=timezone.now(),
        note="Default thresholds (spec 16 §16.4)",
        ctx=ctx,
        dry_run=dry_run,
    )


def record_snapshot(
    vendor: Vendor,
    result: engine.VendorTrustResult,
    *,
    dry_run: bool = False,
) -> ChangeSet:
    """Append the snapshot of `result` for `vendor`, with the rules row of its version.

    Always appends; deciding whether anything changed is the refresh task's job (TRU.2).
    """
    rules = TrustRules.objects.get(version=result.rules_version)
    snapshot = VendorTrustSnapshot(
        vendor=vendor,
        as_of=result.as_of,
        rules=rules,
        rules_version=result.rules_version,
        metrics=result.metrics,
        badges=[b.to_json() for b in result.badges],
    )
    changeset = ChangeSet()
    changeset.create(
        SNAPSHOT_MODEL,
        str(snapshot.public_id),
        {
            "vendor": str(vendor.public_id),
            "as_of": snapshot.as_of,
            "rules_version": snapshot.rules_version,
            "earned": [b.number for b in result.badges if b.earned],
        },
    )
    if not dry_run:
        snapshot.save()
    return changeset


def latest_snapshot(vendor: Vendor) -> VendorTrustSnapshot | None:
    """The newest snapshot of `vendor`: the only one the public pages read."""
    return VendorTrustSnapshot.objects.filter(vendor=vendor).order_by("-as_of", "-id").first()
