"""Ingest services (ING.1): run lifecycle, review decisions, SKU matching, mappings.

Every mutating service takes `ctx` (who acts) and `dry_run`, returns the `ChangeSet` and audits
what it applies. Rows are changed in place so the caller keeps using the instance.
"""

import datetime as dt
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any, Final

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog import engine as catalog_engine
from apps.catalog import services as catalog_services
from apps.catalog.models import Product, ProductStatus, SkuAlias, Variant
from apps.core.audit import AuditContext, record
from apps.core.changeset import ChangeSet
from apps.core.i18n import primary_language
from apps.core.services import audit_changes, run_mutation, save_audited
from apps.vendors.models import Vendor, VendorStatus

from . import engine, extraction
from .catalog_loader import CatalogLoader, LoadReport
from .models import (
    ImportRun,
    ItemChange,
    MappingTable,
    ProductDraft,
    ReviewItem,
    SourceItem,
    SourceMissing,
    SourceState,
    SourceStateVersion,
    SourceVariation,
    SupplierSource,
)

logger = logging.getLogger(__name__)

RUN_CREATED = "ingest.run.created"
RUN_STATUS_CHANGED = "ingest.run.status_changed"
REVIEW_OPENED = "ingest.review.opened"
REVIEW_DECIDED = "ingest.review.decided"
MAPPING_CREATED = "ingest.mapping.created"
MAPPING_UPDATED = "ingest.mapping.updated"
MAPPING_DELETED = "ingest.mapping.deleted"
SOURCE_CREATED = "ingest.source.created"
SOURCE_UPDATED = "ingest.source.updated"
CATALOG_LOAD_RUN = "ingest.catalog_load.run"
DRAFT_PUBLISHED = "ingest.draft.published"
DRAFT_NEEDS_REVIEW = "ingest.draft.needs_review"


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


# Catalogue models a mapping import may point at (ING.4).
MAPPING_TARGET_MODELS = frozenset({"catalog.category", "catalog.term", "catalog.brand"})


def _spec_target(spec: Mapping[str, Any]) -> tuple[ContentType | None, int | None, str]:
    """Resolve a mapping spec's target: a plain `value`, or `target` = model + public_id."""
    target = spec.get("target")
    if not target:
        value = str(spec.get("value", "")).strip()
        if not value:
            raise ValidationError(f"Mapping {spec.get('key')!r} needs a value or a target.")
        return None, None, value
    label = str(target.get("model", "")).lower()
    if label not in MAPPING_TARGET_MODELS:
        raise ValidationError(f"A mapping cannot point at {label!r}.")
    ctype = ContentType.objects.get_by_natural_key(*label.split("."))
    model = ctype.model_class()
    if model is None:
        raise ValidationError(f"Unknown target model {label!r}.")
    try:
        obj = model._default_manager.get(public_id=target.get("public_id"))
    except (ObjectDoesNotExist, ValidationError) as exc:
        raise ValidationError(f"Mapping {spec.get('key')!r}: target {label} not found.") from exc
    return ctype, obj.pk, ""


def import_mappings(
    vendor: Vendor | None,
    specs: Sequence[Mapping[str, Any]],
    *,
    ctx: AuditContext,
    dry_run: bool = False,
) -> ChangeSet:
    """Load a vendor's term mappings (the converted JSON maps of the legacy pipeline, ING.4).

    Each spec: `kind`, `key`, optional `aliases`, and a `value` or a `target` of
    `{"model": "catalog.term", "public_id": "..."}` (a real foreign key, never a per-language
    id). Existing entries (same vendor, kind and normalised key) are updated, so a re-run is
    idempotent. All or nothing; with `dry_run` nothing is written.
    """
    valid_kinds = {k.value for k in MappingTable.Kind}
    changes = ChangeSet()
    with transaction.atomic():
        for spec in specs:
            kind = str(spec.get("kind", ""))
            if kind not in valid_kinds:
                raise ValidationError(f"Unknown mapping kind {kind!r}.")
            key = str(spec.get("key", ""))
            norm = engine.mapping_key(key)
            if not norm:
                raise ValidationError("A mapping key cannot be empty.")
            ctype, target_id, value = _spec_target(spec)
            entry = MappingTable.objects.filter(vendor=vendor, kind=kind, key_norm=norm).first()
            if entry is None:
                entry = MappingTable(vendor=vendor, kind=kind, key=key)
            entry.key = key
            entry.aliases = [str(a) for a in spec.get("aliases", [])]
            entry.target_type = ctype
            entry.target_id = target_id
            entry.target_value = value
            result = save_mapping(entry, ctx=ctx, dry_run=dry_run)
            changes.changes.extend(result.changes)
    return changes


def _entry_value(entry: MappingTable) -> str:
    return entry.target_value or entry.key


def _entry_result(entry: MappingTable) -> dict[str, Any]:
    out: dict[str, Any] = {"key": entry.key}
    if entry.target_type_id and entry.target_id:
        target = entry.target
        out["target"] = f"{entry.target_type.app_label}.{entry.target_type.model}"  # type: ignore[union-attr]
        out["public_id"] = str(getattr(target, "public_id", ""))
    else:
        out["value"] = entry.target_value
    return out


type LoadedRules = tuple[extraction.ExtractionRules, dict[str, MappingTable]]


def load_extraction_rules(vendor: Vendor) -> LoadedRules:
    """The extraction rules of `vendor` (its mappings win over the platform's) and the rows by
    candidate reference."""
    kinds = ("color", "material", "product_type", "country")
    rows = list(
        MappingTable.objects.filter(
            Q(vendor=vendor) | Q(vendor__isnull=True), kind__in=kinds
        ).order_by("pk")
    )
    own = {(r.kind, r.key_norm) for r in rows if r.vendor_id}
    rows = [r for r in rows if r.vendor_id or (r.kind, r.key_norm) not in own]
    entries = [
        engine.MappingEntry(
            vendor_id=r.vendor_id,
            kind=r.kind,
            key=r.key,
            aliases=tuple(str(a) for a in r.aliases),
            target_type=str(r.target_type or ""),
            target_id=r.target_id,
            target_value=r.target_value,
            ref=r.pk,
        )
        for r in rows
    ]
    return extraction.build_rules(entries), {str(r.pk): r for r in rows}


def extract_product(
    vendor: Vendor,
    *,
    title: str | None,
    description: str | None,
    loaded: LoadedRules | None = None,
) -> dict[str, Any]:
    """Extract the optional import fields of one product (read only, so no dry run).

    Returns JSON-safe data: `product_type` (mapped), `model`, `materials`, `colors`, the CAT.22
    fields `length_mm`, `width_mm`, `height_mm`, `material`, `colour`, `country_of_origin`, and
    the other legacy fields. A field the text does not yield is absent.
    """
    rules, by_ref = loaded if loaded is not None else load_extraction_rules(vendor)
    found = extraction.extract_fields(title, description, rules)
    out: dict[str, Any] = dict(found)
    if "product_type" in found:
        out["product_type"] = _entry_result(by_ref[found["product_type"]])
    for plural, singular in (("materials", "material"), ("colors", "colour")):
        if plural in found:
            values = list(dict.fromkeys(_entry_value(by_ref[r]) for r in found[plural]))
            out[plural] = values
            out[singular] = ", ".join(values)
    return out


def load_legacy_catalog(
    bundle: Mapping[str, Any],
    *,
    ctx: AuditContext,
    dry_run: bool = True,
    include_unpublished: bool = False,
    attribute_aliases: Mapping[str, str] | None = None,
) -> LoadReport:
    """MIG.3 part 1: load vendors, catalog and taxonomies from the intermediate format.

    `bundle` is the plain-dict shape documented in `legacy_catalog`. Idempotent: rows found by
    their natural key are skipped, never updated. `include_unpublished` is the vendor's MIG.16
    choice of drafts and archived products (loaded hidden); trashed products are only redirect
    sources. Dry-run (the default) runs the real writes in a transaction that is rolled back, so
    the report's ChangeSet is what a real run does. Every created row is audited (the vendor,
    product and variant services audit their own) and one `ingest.catalog_load.run` entry records
    the run (actor, counts, dry-run or not), also for a dry-run.
    """
    with transaction.atomic():
        report = CatalogLoader(
            bundle,
            ctx=ctx,
            include_unpublished=include_unpublished,
            attribute_aliases=attribute_aliases or {},
        ).run()
        if dry_run:
            transaction.set_rollback(True)
    record(
        ctx,
        CATALOG_LOAD_RUN,
        "ingest.catalogload",
        "legacy",
        None,
        {"dry_run": dry_run, "include_unpublished": include_unpublished, **report.summary()},
    )
    return report


# --- Source state, missing evidence and change history (ING.2)


class StateConflict(Exception):
    """A native id is already owned by another item of the feed: a person must reconcile."""


def state_snapshot(state: SourceState) -> dict[str, Any]:
    return {
        "item": str(state.item.public_id),
        "kind": state.kind,
        "native_id": state.native_id,
        "version": state.version,
        "stock_status": state.stock_status,
        "stock_quantity": state.stock_quantity,
        "state_hash": engine.payload_hash(state.state),
    }


def missing_snapshot(row: SourceMissing) -> dict[str, Any]:
    return {"item": str(row.item.public_id), "url": row.url, "http_status": row.http_status}


def record_source_state(
    item: SourceItem,
    observation: Mapping[str, Any],
    *,
    ctx: AuditContext,
    now: dt.datetime | None = None,
    dry_run: bool = False,
) -> tuple[bool, ChangeSet]:
    """Store a fully parsed observation of `item`; return whether its state changed.

    The observation is validated (`engine.StateError` leaves everything untouched) and merged
    with the stored state: variations no longer listed stay with their last data and `present`
    off. Only a semantic change raises `version` and appends a `SourceStateVersion`; a repeat
    only moves `observed_at`. A native id owned by another item of the feed raises
    `StateConflict` and rolls everything back. A successful read also clears missing evidence.
    """
    now = now or timezone.now()
    new = engine.validate_state(observation)
    changes = ChangeSet()
    with transaction.atomic():
        locked = SourceItem.objects.select_for_update().select_related("source").get(pk=item.pk)
        stored = SourceState.objects.select_for_update().filter(item=locked).first()
        merged = engine.merge_state(stored.state if stored else None, new)
        if stored and stored.kind != merged["kind"]:
            raise engine.StateError("The kind of the item changed: reconciliation required.")
        owner_ids = [merged["native_id"]] if merged["native_id"] is not None else []
        if (
            SourceState.objects.filter(source_id=locked.source_id, native_id__in=owner_ids)
            .exclude(item=locked)
            .exists()
        ):
            raise StateConflict("The native id belongs to another item of the feed.")
        child_ids = [child["native_id"] for child in merged["variations"].values()]
        if (
            SourceVariation.objects.filter(source_id=locked.source_id, native_id__in=child_ids)
            .exclude(item=locked)
            .exists()
        ):
            raise StateConflict("A variation id belongs to another item of the feed.")

        changed = stored is None or stored.state != merged
        row = stored or SourceState(item=locked, source=locked.source)
        row.kind, row.native_id, row.state = merged["kind"], merged["native_id"], merged
        row.stock_status = merged.get("stock_status")
        row.stock_quantity = merged.get("stock_quantity")
        row.observed_at = now
        row.version = (stored.version if stored else 0) + int(changed)
        key = str(locked.public_id)
        if changed and stored:
            changes.update("ingest.sourcestate", key, state_snapshot(stored), state_snapshot(row))
        elif changed:
            changes.create("ingest.sourcestate", key, state_snapshot(row))
        missing = SourceMissing.objects.filter(item=locked).first()
        if missing:
            changes.delete("ingest.sourcemissing", key, missing_snapshot(missing))
        if dry_run:
            return changed, changes
        row.save()
        existing = {
            v.native_id: v
            for v in SourceVariation.objects.filter(
                source_id=locked.source_id, native_id__in=child_ids
            )
        }
        to_create: list[SourceVariation] = []
        to_update: list[SourceVariation] = []
        for child in merged["variations"].values():
            present = bool(child["present"])
            variation = existing.get(child["native_id"])
            if variation is None:
                variation = SourceVariation(
                    item=locked,
                    source=locked.source,
                    native_id=child["native_id"],
                    last_seen_at=now,
                )
                to_create.append(variation)
            else:
                to_update.append(variation)
            variation.state = child
            variation.stock_status = child.get("stock_status")
            variation.stock_quantity = child.get("stock_quantity")
            variation.present = present
            variation.observed_at = now
            if present:
                variation.last_seen_at = now
            variation.updated_at = timezone.now()
        SourceVariation.objects.bulk_create(to_create)
        SourceVariation.objects.bulk_update(
            to_update,
            [
                "state",
                "stock_status",
                "stock_quantity",
                "present",
                "observed_at",
                "last_seen_at",
                "updated_at",
            ],
        )
        if changed:
            SourceStateVersion.objects.create(
                item=locked, version=row.version, state=merged, observed_at=now
            )
        if missing:
            missing.delete()
    return changed, changes


def record_missing(
    item: SourceItem,
    url: str,
    http_status: int,
    *,
    ctx: AuditContext,
    now: dt.datetime | None = None,
    dry_run: bool = False,
) -> ChangeSet:
    """Record that the supplier's page for `item` answered an error (404): evidence for the
    rule "the supplier is master of stock". Repeats only move `last_seen_at`."""
    now = now or timezone.now()
    changes = ChangeSet()
    with transaction.atomic():
        locked = SourceItem.objects.select_for_update().get(pk=item.pk)
        row = SourceMissing.objects.filter(item=locked).first()
        new = SourceMissing(
            item=locked,
            url=url,
            http_status=http_status,
            first_seen_at=row.first_seen_at if row else now,
            last_seen_at=now,
        )
        new.full_clean(exclude=["item"], validate_unique=False)
        key = str(locked.public_id)
        if row:
            changes.update(
                "ingest.sourcemissing", key, missing_snapshot(row), missing_snapshot(new)
            )
        else:
            changes.create("ingest.sourcemissing", key, missing_snapshot(new))
        if dry_run:
            return changes
        if row:
            row.url, row.http_status, row.last_seen_at = url, http_status, now
            row.save()
        else:
            new.save()
    return changes


def clear_missing(item: SourceItem, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    changes = ChangeSet()
    row = SourceMissing.objects.filter(item=item).first()
    if row is None:
        return changes
    changes.delete("ingest.sourcemissing", str(item.public_id), missing_snapshot(row))
    if not dry_run:
        row.delete()
    return changes


def missing_since(item_ids: Sequence[int]) -> dict[int, dt.datetime]:
    """`{item id: when it was last found missing}` for the items whose page is missing."""
    return dict(
        SourceMissing.objects.filter(item_id__in=set(item_ids)).values_list(
            "item_id", "last_seen_at"
        )
    )


def record_item_change(
    item: SourceItem,
    values: Mapping[str, Any],
    *,
    ctx: AuditContext,
    now: dt.datetime | None = None,
    dry_run: bool = False,
) -> ChangeSet:
    """Append a change with the fields of `values` that differ from their effective value.

    Nothing is appended when nothing differs. The history is never edited or deleted."""
    now = now or timezone.now()
    changes = ChangeSet()
    with transaction.atomic():
        locked = SourceItem.objects.select_for_update().get(pk=item.pk)
        current = latest_item_fields(locked.pk)
        diff = engine.changed_fields(current, engine.canonical_state(values))
        if not diff:
            return changes
        last = ItemChange.objects.filter(item=locked).order_by("-version").first()
        row = ItemChange(
            item=locked,
            version=(last.version if last else 0) + 1,
            changed_fields=dict.fromkeys(diff, True),
            new_values=diff,
            recorded_at=now,
        )
        changes.create(
            "ingest.itemchange",
            str(locked.public_id),
            {"item": str(locked.public_id), "version": row.version, "fields": sorted(diff)},
        )
        if dry_run:
            return changes
        row.save()
    return changes


def latest_changes(
    item_ids: Sequence[int], fields: Sequence[str] | None = None
) -> dict[int, dict[str, Any]]:
    """`{item id: {field: last flagged value}}`, read from the history only."""
    rows: dict[int, list[tuple[Mapping[str, Any], Mapping[str, Any]]]] = {}
    history = (
        ItemChange.objects.filter(item_id__in=set(item_ids))
        .order_by("item_id", "version", "id")
        .values_list("item_id", "changed_fields", "new_values")
    )
    for item_id, flagged, values in history:
        rows.setdefault(item_id, []).append((flagged, values))
    return {item_id: engine.latest_field_values(r, fields) for item_id, r in rows.items()}


def latest_item_fields(item_id: int, fields: Sequence[str] | None = None) -> dict[str, Any]:
    """`{field: last flagged value}` of one item in a single query (`DISTINCT ON (field)`).

    Same result as `latest_changes([item_id], fields)`, without reading the whole history."""
    sql = (
        "SELECT DISTINCT ON (kv.key) kv.key, kv.value "  # noqa: S608 (model constant, values bound)
        f"FROM {ItemChange._meta.db_table} c "
        "CROSS JOIN LATERAL jsonb_each(c.new_values) AS kv "
        "WHERE c.item_id = %s AND (c.changed_fields -> kv.key) = 'true'::jsonb"
    )
    params: list[Any] = [item_id]
    if fields is not None:
        sql += " AND kv.key = ANY(%s)"
        params.append(list(fields))
    sql += " ORDER BY kv.key, c.version DESC, c.id DESC"
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return {key: json.loads(value) for key, value in cursor.fetchall()}


# --- Publishing drafts (ING.7)


@dataclass
class PublishReport:
    """The outcome of `publish_drafts`: what changed (or would, on a dry run), the SKUs of the
    drafts published, and the SKUs skipped with the reason. A skipped draft writes nothing."""

    changes: ChangeSet = dataclass_field(default_factory=ChangeSet)
    published: list[str] = dataclass_field(default_factory=list)
    skipped: dict[str, str] = dataclass_field(default_factory=dict)
    refused: set[str] = dataclass_field(default_factory=set)  # skipped for good: needs a person
    failed: set[str] = dataclass_field(default_factory=set)  # an unexpected error: logged, counted


class _Refused(Exception):
    """Rolls back the writes of one draft whose product the catalog refused to publish."""


def draft_snapshot(draft: ProductDraft) -> dict[str, Any]:
    return {
        "vendor": _vendor_key(draft.item.vendor),
        "status": draft.status,
        "product": str(draft.item.product.public_id) if draft.item.product else None,
    }


def publish_drafts(
    drafts: Sequence[ProductDraft], *, ctx: AuditContext, dry_run: bool = True
) -> PublishReport:
    """Turn `ready` drafts into published products in the primary language, in one step each.

    Everything goes through `catalog.services` (`upsert_products`, then `publish`): a new SKU
    becomes a product with the EN text only (D-18 bis); a SKU that matches an existing product
    updates its EN text and leaves the other languages, for example the Italian texts migrated
    by MIG.3, published and untouched. Each draft is its own transaction: one the catalog
    refuses (a publishing rule, a row error) is skipped with its reason and writes nothing; the
    others go on. Written: the draft turns `published`, its item points to the product, and
    `ingest.draft.published` is audited. `dry_run` (the default, spec 08) returns the ChangeSet
    of a real run and writes nothing, and reports the same published and skipped SKUs. The draft
    row is locked and its status read again inside its transaction, so a draft changed or
    published meanwhile is skipped; a retired product is never republished (it is `refused`).
    `refused` holds the SKUs the catalog or the plan turned down for good; `skipped` has every
    SKU not published, with the reason. The per-source `write_enabled` flag, the active vendor
    and the schedule belong to `publish_ready_drafts`, not to this low-level service.
    """
    report = PublishReport()
    language = primary_language()
    for draft in drafts:
        sku = draft.item.sku
        try:
            with transaction.atomic():
                outcome = _publish_locked(draft, language, ctx=ctx, dry_run=dry_run)
        except _Refused as refused:
            report.skipped[sku] = str(refused)
            report.refused.add(sku)
            continue
        except IntegrityError:  # a concurrent caller created the same SKU first
            report.skipped[sku] = "The SKU was created at the same time; try again."
            continue
        except Exception as exc:  # one odd draft must not stop the batch (job guard)
            logger.exception("ingest.publish_drafts: draft %s failed", sku)
            report.skipped[sku] = f"Unexpected error ({type(exc).__name__}); see the log."
            report.failed.add(sku)
            continue
        if isinstance(outcome, str):
            report.skipped[sku] = outcome
            continue
        report.changes.changes.extend(outcome.changes)
        report.published.append(sku)
    return report


def _publish_locked(
    draft: ProductDraft, language: str, *, ctx: AuditContext, dry_run: bool
) -> ChangeSet | str:
    """Publish one draft inside the caller's transaction. Returns the changes, or the reason
    the draft was left alone (not ready any more, or no valid payload)."""
    locked = (
        ProductDraft.objects.select_for_update(of=("self",))
        .select_related("item__vendor", "item__product")
        .get(pk=draft.pk)
    )
    if not engine.is_publishable_status(locked.status):
        return f"The draft is {locked.status}, not ready."
    payload = engine.plan_draft_product(
        locked.item.sku, locked.extracted, locked.enriched, language=language
    )
    if isinstance(payload, engine.DraftProblem):
        raise _Refused(payload.reason)
    changes = _publish_one(locked, payload, language, ctx=ctx, dry_run=dry_run)
    if not dry_run:
        draft.status = locked.status
    return changes


def _publish_one(
    draft: ProductDraft,
    payload: catalog_engine.ProductIn,
    language: str,
    *,
    ctx: AuditContext,
    dry_run: bool,
) -> ChangeSet:
    vendor = draft.item.vendor
    match = catalog_services.match_skus(vendor, [payload.sku])[payload.sku]
    if isinstance(match, Product) and match.status == ProductStatus.RETIRED:
        # Staff retired it; a late draft must not bring it back (spec 03 allows it by hand).
        raise _Refused("The product is retired; a draft does not republish it.")
    upserted = catalog_services.upsert_products(vendor, [payload], ctx=ctx, dry_run=dry_run)
    if upserted.errors:
        raise _Refused("; ".join(m for e in upserted.errors for v in e.errors.values() for m in v))
    changes = upserted.changes
    product = match if isinstance(match, Product) else None
    if product is None and not dry_run:
        found = catalog_services.match_skus(vendor, [payload.sku])[payload.sku]
        product = found if isinstance(found, Product) else None
    if product is not None:
        status = catalog_services.publish([product], ctx=ctx, dry_run=dry_run)
        refused = {k: v for r in status.refused.values() for k, v in r.items()}
        if dry_run and refused:
            # The EN text is not stored yet on a dry run, so the catalog sees a missing one that
            # the real run will have written: leave that rule out so both runs agree.
            missing = set(
                catalog_engine.publication_errors("catalog_only", None, None, [], language)
            )
            refused = {k: [m for m in v if m not in missing] for k, v in refused.items()}
            refused = {k: v for k, v in refused.items() if v}
            if not refused:
                status = catalog_services.StatusReport()
                status.changes.update(
                    catalog_services.PRODUCT_MODEL,
                    str(product.public_id),
                    {"status": product.status},
                    {"status": ProductStatus.PUBLISHED},
                )
        if refused:
            raise _Refused("; ".join(m for v in refused.values() for m in v))
        changes.changes.extend(status.changes.changes)
    before = draft_snapshot(draft)
    after = {**before, "status": ProductDraft.Status.PUBLISHED}
    if product is not None:
        after["product"] = str(product.public_id)
    item_change = ChangeSet()
    item_change.update("ingest.productdraft", str(draft.public_id), before, after)
    changes.changes.extend(item_change.changes)
    if not dry_run:
        draft.status = ProductDraft.Status.PUBLISHED
        draft.save(update_fields=["status", "updated_at"])
        draft.item.product = product
        draft.item.save(update_fields=["product", "updated_at"])
        audit_changes(item_change, ctx=ctx, action=DRAFT_PUBLISHED)
    return changes


PUBLISH_BATCH: Final = 200


def publish_ready_drafts(
    *, ctx: AuditContext, dry_run: bool = False, limit: int = PUBLISH_BATCH
) -> PublishReport:
    """Scheduled publication (ING.7): publish the `ready` drafts of every active source that
    allows writes (`write_enabled`) and whose vendor is active, oldest first, at most `limit`
    per call. A source without the flag is never touched; a rerun finds nothing new. A draft the
    catalog refuses for good (`report.refused`) is moved to `needs_review` with the reason in the
    audit entry, and a person decides. A draft that raises anything else is logged, counted in
    `report.failed` and moved to `needs_review` the same way, so it cannot starve the queue."""
    drafts = list(
        ProductDraft.objects.filter(
            status=ProductDraft.Status.READY,
            item__source__is_active=True,
            item__source__write_enabled=True,
            item__vendor__status=VendorStatus.ACTIVE,
        )
        .select_related("item__vendor", "item__product")
        .order_by("pk")[:limit]
    )
    report = publish_drafts(drafts, ctx=ctx, dry_run=dry_run)
    if not dry_run:
        for draft in drafts:
            sku = draft.item.sku
            if sku not in report.refused and sku not in report.failed:
                continue
            try:  # a failure here must not skip the drafts after this one
                _send_to_review(draft, report.skipped[sku], ctx=ctx)
            except Exception:
                logger.exception("ingest.publish_ready_drafts: draft %s not moved aside", sku)
                report.failed.add(sku)
    return report


def _send_to_review(draft: ProductDraft, reason: str, *, ctx: AuditContext) -> None:
    """A draft the catalog refuses for good leaves the queue (`needs_review`) with its reason in
    the audit entry, so it is not retried every run and does not starve the batch."""
    with transaction.atomic():
        locked = ProductDraft.objects.select_for_update(of=("self",)).get(pk=draft.pk)
        if not engine.is_publishable_status(locked.status):
            return
        before = draft_snapshot(draft)
        locked.status = ProductDraft.Status.NEEDS_REVIEW
        locked.save(update_fields=["status", "updated_at"])
        draft.status = locked.status
        changes = ChangeSet()
        changes.update(
            "ingest.productdraft",
            str(draft.public_id),
            before,
            {**before, "status": locked.status, "reason": reason},
        )
        audit_changes(changes, ctx=ctx, action=DRAFT_NEEDS_REVIEW)
