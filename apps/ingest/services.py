"""Ingest services (ING.1): run lifecycle, review decisions, SKU matching, mappings.

Every mutating service takes `ctx` (who acts) and `dry_run`, returns the `ChangeSet` and audits
what it applies. Rows are changed in place so the caller keeps using the instance.
"""

import datetime as dt
from collections.abc import Mapping
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.models import Product, SkuAlias, Variant
from apps.core.audit import AuditContext, record
from apps.core.changeset import ChangeSet
from apps.core.models import AuditLog
from apps.core.services import enqueue_email, run_mutation, save_audited
from apps.vendors.models import Vendor, VendorMember

from . import engine
from .models import (
    ImportRun,
    MappingTable,
    MigrationChoice,
    MigrationRun,
    ReviewItem,
    SourceItem,
    SupplierSource,
)

RUN_CREATED = "ingest.run.created"
RUN_STATUS_CHANGED = "ingest.run.status_changed"
REVIEW_OPENED = "ingest.review.opened"
REVIEW_DECIDED = "ingest.review.decided"
MAPPING_CREATED = "ingest.mapping.created"
MAPPING_UPDATED = "ingest.mapping.updated"
MAPPING_DELETED = "ingest.mapping.deleted"
SOURCE_CREATED = "ingest.source.created"
SOURCE_UPDATED = "ingest.source.updated"
MIGRATION_RUN_CREATED = "ingest.migration_run.created"
MIGRATION_CHOICE_RECORDED = "ingest.migration_choice.recorded"
MIGRATION_CHOICE_DEFAULTED = "ingest.migration_choice.defaulted"
MIGRATION_CHOICE_REMINDER = "ingest.migration_choice.reminder_sent"
MIGRATION_CHOICE_DENIED = "ingest.migration_choice.access_denied"
MIGRATION_REMINDER_TEMPLATE = "ingest.migration_choice_reminder"


class WriteNotEnabled(Exception):
    """A write run was asked for a source whose `write_enabled` flag is off."""


class InvalidTransition(Exception):
    """The run status change is not allowed by the status machine."""


class BlockedByReview(Exception):
    """The run has open blocking review items and cannot succeed."""


class InvalidDecision(Exception):
    """The review item cannot take this decision from its current one."""


# --- Snapshots (vendor-owned: they name the vendor by public id for the audit trail)


def _vendor_key(vendor: Vendor | None) -> str:
    return str(vendor.public_id) if vendor else ""


def source_snapshot(source: SupplierSource) -> dict[str, Any]:
    return {
        "vendor": _vendor_key(source.vendor),
        "code": source.code,
        "adapter_code": source.adapter_code,
        # Credentials are named by env reference, never stored in `config`; even so the audit
        # trail keeps only the keys and a digest, so a changed value still shows as a change.
        "config_keys": sorted(source.config),
        "config_hash": engine.payload_hash(source.config),
        "schedule": source.schedule,
        "is_active": source.is_active,
        "write_enabled": source.write_enabled,
    }


def run_snapshot(run: ImportRun) -> dict[str, Any]:
    return {
        "vendor": _vendor_key(run.source.vendor),
        "source": run.source.code,
        "kind": run.kind,
        "status": run.status,
        "dry_run": run.dry_run,
        **{name: getattr(run, name) for name in engine.RUN_COUNTERS},
    }


def review_snapshot(item: ReviewItem) -> dict[str, Any]:
    return {
        "vendor": _vendor_key(item.run.source.vendor),
        "run": str(item.run.public_id),
        "kind": item.kind,
        "blocking": item.blocking,
        "decision": item.decision,
        "note": item.note,
        "resolved_by": item.resolved_by_id,
    }


def mapping_snapshot(entry: MappingTable) -> dict[str, Any]:
    return {
        "vendor": _vendor_key(entry.vendor),
        "kind": entry.kind,
        "key": entry.key,
        "aliases": entry.aliases,
        "target_type": str(entry.target_type) if entry.target_type_id else "",
        "target_id": entry.target_id,
        "target_value": entry.target_value,
    }


# --- Sources


def save_source(source: SupplierSource, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Create or update a supplier source (the admin and setup call this)."""
    source.full_clean(validate_unique=False)
    return save_audited(
        source,
        source_snapshot,
        key=str(source.public_id),
        ctx=ctx,
        created=SOURCE_CREATED,
        updated=SOURCE_UPDATED,
        dry_run=dry_run,
    )


# --- Runs


def queue_run(
    source: SupplierSource,
    kind: str,
    *,
    ctx: AuditContext,
    write: bool = False,
    dry_run: bool = False,
) -> tuple[ImportRun, ChangeSet]:
    """Queue a run. It is a dry run unless `write` is asked and the source allows it.

    `dry_run=True` previews the new row without storing it (the returned run is unsaved).
    """
    if write and not source.write_enabled:
        raise WriteNotEnabled(f"{source} is not enabled for write runs")
    run = ImportRun(source=source, kind=kind, dry_run=not write)
    changes = save_audited(
        run,
        run_snapshot,
        key=str(run.public_id),
        ctx=ctx,
        created=RUN_CREATED,
        updated=RUN_STATUS_CHANGED,
        dry_run=dry_run,
    )
    return run, changes


def open_blocking_items(run: ImportRun) -> int:
    return ReviewItem.objects.filter(
        run=run, blocking=True, decision__in=engine.OPEN_DECISIONS
    ).count()


def transition_run(
    run: ImportRun,
    target: str,
    *,
    ctx: AuditContext,
    counters: Mapping[str, int] | None = None,
    log: str = "",
    dry_run: bool = False,
) -> ChangeSet:
    """Move `run` along the status machine, optionally setting its final counters and log.

    Raises InvalidTransition, and BlockedByReview when asked to succeed with open blocking
    review items (an ambiguous match stops the run with a report).
    """
    unknown = set(counters or ()) - set(engine.RUN_COUNTERS)
    if unknown:
        raise ValueError(f"unknown counters: {sorted(unknown)}")
    with transaction.atomic():
        locked = (
            ImportRun.objects.select_for_update().select_related("source__vendor").get(pk=run.pk)
        )
        if not engine.can_transition_run(locked.status, target):
            raise InvalidTransition(f"{locked.status} -> {target}")
        if target == engine.SUCCEEDED and open_blocking_items(locked):
            raise BlockedByReview(f"{open_blocking_items(locked)} blocking review item(s) open")
        if target == engine.RUNNING and not locked.dry_run and not locked.source.write_enabled:
            raise WriteNotEnabled(f"{locked.source} is no longer enabled for write runs")
        before = run_snapshot(locked)
        now = timezone.now()
        locked.status = target
        if target == engine.RUNNING:
            locked.started_at = now
        if engine.is_terminal_run(target):
            locked.finished_at = now
        for name, value in (counters or {}).items():
            setattr(locked, name, value)
        if log:
            locked.log = log
        changes = ChangeSet()
        changes.update("ingest.importrun", str(locked.public_id), before, run_snapshot(locked))
        run_mutation(changes, locked.save, ctx=ctx, action=RUN_STATUS_CHANGED, dry_run=dry_run)
        if not dry_run:
            for field in locked._meta.concrete_fields:
                setattr(run, field.attname, getattr(locked, field.attname))
    return changes


# --- SKU matching


def match_item(
    run: ImportRun, item: SourceItem, *, ctx: AuditContext, dry_run: bool = False
) -> tuple[engine.SkuMatch, ChangeSet]:
    """Match `item` to the vendor's products by SKU match key, then alias history.

    An ambiguous match, or another feed row of the vendor with the same SKU, opens one blocking
    `duplicate_sku` review item for the run (none is opened twice) and nothing else is written.
    The run must be queued or running and the item must belong to its vendor.
    """
    with transaction.atomic():
        locked = (
            ImportRun.objects.select_for_update().select_related("source__vendor").get(pk=run.pk)
        )
        if engine.is_terminal_run(locked.status):
            raise InvalidTransition(f"run is {locked.status}")
        vendor_id = locked.source.vendor_id
        if item.vendor_id != vendor_id:
            raise ValidationError("The item belongs to another vendor than the run.")
        key = item.sku_key or engine.mapping_key(item.sku)
        by_key: dict[str, list[int]] = {}
        for pk in Product.objects.filter(vendor_id=vendor_id, sku_key=key).values_list(
            "pk", flat=True
        ):
            by_key.setdefault(key, []).append(pk)
        for product_id in Variant.objects.filter(vendor_id=vendor_id, sku_key=key).values_list(
            "product_id", flat=True
        ):
            by_key.setdefault(key, []).append(product_id)
        by_alias: dict[str, list[int]] = {}
        for alias in SkuAlias.objects.filter(vendor_id=vendor_id, sku_key=key).select_related(
            "variant"
        ):
            owner = alias.product_id or (alias.variant.product_id if alias.variant else None)
            if owner is not None:
                by_alias.setdefault(key, []).append(owner)

        match = engine.match_sku(key, by_key=by_key, by_alias=by_alias)
        twins = (
            list(
                SourceItem.objects.filter(vendor_id=vendor_id, sku_key=key)
                .exclude(pk=item.pk)
                .values_list("pk", flat=True)
            )
            if key
            else []
        )
        changes = ChangeSet()
        if match.kind != "ambiguous" and not twins:
            return match, changes
        if match.kind != "ambiguous":
            match = engine.SkuMatch("ambiguous", match.product_ids)
        if ReviewItem.objects.filter(
            run=locked,
            item=item,
            kind=ReviewItem.Kind.DUPLICATE_SKU,
            decision__in=engine.OPEN_DECISIONS,
        ).exists():
            return match, changes
        review = ReviewItem(
            run=locked,
            item=item,
            kind=ReviewItem.Kind.DUPLICATE_SKU,
            blocking=engine.is_blocking("duplicate_sku"),
            payload={
                "sku": item.sku,
                "sku_key": key,
                "product_ids": list(match.product_ids),
                "item_ids": twins,
            },
        )
        review.full_clean()
        changes.create("ingest.reviewitem", str(review.public_id), review_snapshot(review))
        run_mutation(changes, review.save, ctx=ctx, action=REVIEW_OPENED, dry_run=dry_run)
    return match, changes


def decide_review(
    review: ReviewItem,
    decision: str,
    *,
    user: User,
    note: str = "",
    ctx: AuditContext,
    dry_run: bool = False,
) -> ChangeSet:
    """Record a person's decision. Accepted and rejected are final; deferred can be decided."""
    with transaction.atomic():
        locked = (
            ReviewItem.objects.select_for_update()
            .select_related("run__source__vendor")
            .get(pk=review.pk)
        )
        if not engine.can_decide(locked.decision, decision):
            raise InvalidDecision(f"{locked.decision} -> {decision}")
        before = review_snapshot(locked)
        locked.decision = decision
        locked.note = note or locked.note
        locked.resolved_by = user
        locked.resolved_at = timezone.now()
        changes = ChangeSet()
        changes.update("ingest.reviewitem", str(locked.public_id), before, review_snapshot(locked))
        run_mutation(changes, locked.save, ctx=ctx, action=REVIEW_DECIDED, dry_run=dry_run)
        if not dry_run:
            for field in locked._meta.concrete_fields:
                setattr(review, field.attname, getattr(locked, field.attname))
    return changes


# --- Mappings


def lookup_mapping(vendor: Vendor, kind: str, raw: str) -> MappingTable | None:
    """The mapping that answers `raw` for `vendor` (vendor entry first, then the platform's)."""
    rows = list(MappingTable.objects.filter(Q(vendor=vendor) | Q(vendor__isnull=True), kind=kind))
    by_pk = {row.pk: row for row in rows}
    entries = [
        engine.MappingEntry(
            vendor_id=row.vendor_id,
            kind=row.kind,
            key=row.key,
            aliases=tuple(str(a) for a in row.aliases),
            target_type=str(row.target_type or ""),
            target_id=row.target_id,
            target_value=row.target_value,
            ref=row.pk,
        )
        for row in rows
    ]
    found = engine.resolve_mapping(entries, vendor_id=vendor.pk, kind=kind, raw=raw)
    return by_pk[found.ref] if found and found.ref is not None else None


def save_mapping(entry: MappingTable, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Create or update a mapping entry; it needs a target object or a target value."""
    if not entry.target_value and not (entry.target_type_id and entry.target_id):
        raise ValidationError("A mapping needs a target object or a target value.")
    target = entry.target if entry.target_type_id else None
    target_vendor = getattr(target, "vendor_id", None)
    if target_vendor is not None and target_vendor != entry.vendor_id:
        raise ValidationError("A mapping can only point at an object of its own vendor.")
    entry.key_norm = engine.mapping_key(entry.key)
    if not entry.key_norm:
        raise ValidationError({"key": "A mapping key cannot be empty."})
    return save_audited(
        entry,
        mapping_snapshot,
        key=str(entry.public_id),
        ctx=ctx,
        created=MAPPING_CREATED,
        updated=MAPPING_UPDATED,
        dry_run=dry_run,
    )


def delete_mapping(entry: MappingTable, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    changes = ChangeSet()
    changes.delete("ingest.mappingtable", str(entry.public_id), mapping_snapshot(entry))
    return run_mutation(changes, entry.delete, ctx=ctx, action=MAPPING_DELETED, dry_run=dry_run)


# --- Vendor migration choices (MIG.16)


class ChoiceClosed(Exception):
    """The vendor's choice deadline has passed: the defaults apply, the vendor can no longer
    change them (staff can, on the vendor's behalf)."""


class ChoiceMissing(Exception):
    """No migration choice is recorded for the vendor and run: the dry run must not start."""


def choice_snapshot(choice: MigrationChoice) -> dict[str, Any]:
    return {
        "vendor": _vendor_key(choice.vendor),
        "run": choice.run.label,
        "version": choice.version,
        **engine.choice_snapshot(choice.values),
        "source": choice.source,
    }


def create_run(
    label: str, cutover_at: dt.datetime, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Plan a migration cutover; vendors record their choices against it."""
    run = MigrationRun(label=label, cutover_at=cutover_at)
    run.full_clean(validate_unique=True)
    changes = ChangeSet()
    snapshot = {"label": label, "cutover_at": cutover_at, "choice_deadline": run.choice_deadline}
    changes.create(MigrationRun._meta.label_lower, str(run.public_id), snapshot)
    return run_mutation(changes, run.save, ctx=ctx, action=MIGRATION_RUN_CREATED, dry_run=dry_run)


def active_run(now: dt.datetime) -> MigrationRun | None:
    """The next cutover not yet passed (the one vendors are choosing for)."""
    return MigrationRun.objects.filter(cutover_at__gt=now).order_by("cutover_at", "id").first()


def current_choice(vendor: Vendor, run: MigrationRun) -> MigrationChoice | None:
    """The latest version of the vendor's choice for `run`, if any."""
    return (
        MigrationChoice.objects.filter(vendor=vendor, run=run)
        .select_related("vendor", "run")
        .order_by("-version")
        .first()
    )


def choice_history(vendor: Vendor, run: MigrationRun) -> list[MigrationChoice]:
    return list(
        MigrationChoice.objects.filter(vendor=vendor, run=run)
        .select_related("vendor", "run")
        .order_by("version")
    )


def require_recorded_choice(vendor: Vendor, run: MigrationRun) -> MigrationChoice:
    """The guard the dry run and the loaders (MIG.3, MIG.4) call first: a recorded choice
    (a default applied after the deadline counts) or `ChoiceMissing`."""
    choice = current_choice(vendor, run)
    if choice is None:
        raise ChoiceMissing(f"No migration choice recorded for {vendor} and run {run.label}.")
    return choice


def record_choice(
    vendor: Vendor,
    run: MigrationRun,
    values: engine.ChoiceValues,
    *,
    source: str,
    ctx: AuditContext,
    actor: User | None = None,
    now: dt.datetime | None = None,
    dry_run: bool = False,
) -> ChangeSet:
    """Record a new version of the vendor's choice and audit it with the previous value.

    Values are checked against the allowed lists (`ValidationError` otherwise). A vendor cannot
    record after the deadline (`ChoiceClosed`); staff and the deadline job can. Saving the same
    values from the same source again changes nothing.
    """
    errors = engine.validate_choice(values)
    if errors:
        raise ValidationError(errors)
    if source not in engine.CHOICE_SOURCES:
        raise ValueError(f"unknown choice source {source!r}")
    moment = now or timezone.now()
    if source == engine.SOURCE_VENDOR and moment >= run.choice_deadline:
        raise ChoiceClosed(f"The choice for {run.label} closed on {run.choice_deadline:%Y-%m-%d}.")
    changes = ChangeSet()
    with transaction.atomic():
        Vendor.objects.select_for_update().get(pk=vendor.pk)  # serialises versions per vendor
        previous = current_choice(vendor, run)
        if previous is not None and previous.values == values and previous.source == source:
            return changes
        choice = MigrationChoice(
            vendor=vendor,
            run=run,
            version=1 if previous is None else previous.version + 1,
            period_months=values.period_months,
            data_types=list(values.data_types),
            include_unpublished=values.include_unpublished,
            login_window_months=values.login_window_months,
            source=source,
            decided_by=actor if source != engine.SOURCE_DEFAULT else None,
        )
        changes.create(
            MigrationChoice._meta.label_lower,
            f"{vendor.public_id}/{run.label}/{choice.version}",
            choice_snapshot(choice),
            supersedes=None if previous is None else choice_snapshot(previous),
        )
        action = (
            MIGRATION_CHOICE_DEFAULTED
            if source == engine.SOURCE_DEFAULT
            else MIGRATION_CHOICE_RECORDED
        )
        return run_mutation(changes, choice.save, ctx=ctx, action=action, dry_run=dry_run)


def apply_defaults(
    run: MigrationRun, *, now: dt.datetime, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """After the deadline, record the defaults for every open vendor that has not chosen.

    Each default is a choice version with source "default" and its own audit entry. Running it
    again changes nothing.
    """
    combined = ChangeSet()
    if now < run.choice_deadline:
        return combined
    chosen = MigrationChoice.objects.filter(run=run).values("vendor_id")
    pending = Vendor.objects.exclude(status=Vendor.Status.CLOSED).exclude(pk__in=chosen)
    for vendor in pending.order_by("pk"):
        result = record_choice(
            vendor,
            run,
            engine.DEFAULT_CHOICE,
            source=engine.SOURCE_DEFAULT,
            ctx=ctx,
            now=now,
            dry_run=dry_run,
        )
        combined.changes.extend(result.changes)
    return combined


def _reminder_target(run: MigrationRun, vendor: Vendor) -> str:
    return f"{run.public_id}/{vendor.public_id}"


def send_reminders(
    run: MigrationRun, *, now: dt.datetime, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Remind the owners of every open vendor with no choice yet, before the deadline.

    One reminder per vendor and offset (14, 7 and 1 days before the deadline), through the email
    outbox, each with one audit entry (vendor, run, offset and number of recipients; never an
    address). Running it again in the same window sends nothing.
    """
    combined = ChangeSet()
    offset = engine.due_reminder(now, run.choice_deadline)
    if offset is None:
        return combined
    chosen = MigrationChoice.objects.filter(run=run).values("vendor_id")
    pending = Vendor.objects.exclude(status=Vendor.Status.CLOSED).exclude(pk__in=chosen)
    for vendor in pending.order_by("pk"):
        target = _reminder_target(run, vendor)
        if AuditLog.objects.filter(
            action=MIGRATION_CHOICE_REMINDER, target_key=target, after__offset_days=offset
        ).exists():
            continue
        owners = list(
            VendorMember.objects.filter(vendor=vendor, role=VendorMember.Role.OWNER).select_related(
                "user"
            )
        )
        after = {
            "vendor": _vendor_key(vendor),
            "run": run.label,
            "offset_days": offset,
            "recipients": len(owners),
        }
        combined.create(MigrationChoice._meta.label_lower + ".reminder", target, after)
        if dry_run:
            continue
        with transaction.atomic():
            for member in owners:
                enqueue_email(
                    recipient=member.user.email,
                    template_key=MIGRATION_REMINDER_TEMPLATE,
                    language=member.user.preferred_language,
                    context={
                        "vendor_name": vendor.display_name,
                        "deadline": run.choice_deadline.date().isoformat(),
                        "days_left": (run.choice_deadline - now).days,
                    },
                    idempotency_key=f"mig16:{run.public_id}:{vendor.public_id}:{offset}:"
                    f"{member.user.public_id}",
                )
            record(
                ctx,
                MIGRATION_CHOICE_REMINDER,
                MigrationChoice._meta.label_lower,
                target,
                None,
                after,
            )
    return combined


def record_denied_access(*, ctx: AuditContext, own_vendor: Vendor, requested: str) -> None:
    """Audit an attempt to read or change another vendor's migration choice (answered 404).

    Attributed to the caller's own vendor; the requested id is kept as typed, cut to 64 chars.
    """
    after = {"vendor": _vendor_key(own_vendor), "requested_vendor": requested[:64]}
    record(ctx, MIGRATION_CHOICE_DENIED, MigrationChoice._meta.label_lower, "", None, after)
