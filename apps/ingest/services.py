"""Ingest services (ING.1): run lifecycle, review decisions, SKU matching, mappings.

Every mutating service takes `ctx` (who acts) and `dry_run`, returns the `ChangeSet` and audits
what it applies. Rows are changed in place so the caller keeps using the instance.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.models import Product, SkuAlias, Variant
from apps.core.audit import AuditContext, record
from apps.core.changeset import ChangeSet
from apps.core.services import run_mutation, save_audited
from apps.vendors.models import Vendor

from . import engine, extraction
from .catalog_loader import CatalogLoader, LoadReport
from .models import ImportRun, MappingTable, ReviewItem, SourceItem, SupplierSource

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
