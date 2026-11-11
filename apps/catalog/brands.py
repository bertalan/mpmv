"""Brand proposals, review and merge (CAT.20 2/3).

Brands are one shared list. A vendor picks any approved brand (and its own pending proposals) and
proposes a missing one; Catalog staff approve, reject or merge it into an existing brand. Brands
that arrive with a feed or import are proposed as one batch per run, outside the per-vendor limit
on manual proposals (`settings.MPMV_BRAND_PENDING_LIMIT`, default 20).

Every mutation takes `dry_run` and returns a `ChangeSet`, validates before it writes, and audits
in the same transaction. Staff edits of brands and manufacturers (`save_*`, `delete_*`) are
audited too; the admin calls these.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db import transaction
from django.db.models import Q, QuerySet

from apps.core import translations
from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.services import audit_changes, run_mutation, save_audited
from apps.vendors.models import Vendor

from . import engine, services
from .models import Brand, BrandBatch, Manufacturer, Product, Redirect

BRAND_CREATED = "catalog.brand.created"
BRAND_UPDATED = "catalog.brand.updated"
BRAND_DELETED = "catalog.brand.deleted"
BRAND_PROPOSED = "catalog.brand.proposed"
BRAND_APPROVED = "catalog.brand.approved"
BRAND_REJECTED = "catalog.brand.rejected"
BRAND_MERGED = "catalog.brand.merged"
BATCH_PROPOSED = "catalog.brandbatch.proposed"
BATCH_APPROVED = "catalog.brandbatch.approved"
BATCH_REJECTED = "catalog.brandbatch.rejected"
MANUFACTURER_CREATED = "catalog.manufacturer.created"
MANUFACTURER_UPDATED = "catalog.manufacturer.updated"
MANUFACTURER_DELETED = "catalog.manufacturer.deleted"

BRAND_MODEL = "catalog.brand"
BATCH_MODEL = "catalog.brandbatch"
MANUFACTURER_MODEL = "catalog.manufacturer"


@dataclass
class BrandOutcome:
    """A proposal: the brand (None in a dry run) and what changed."""

    brand: Brand | None
    changes: ChangeSet


@dataclass
class BatchOutcome:
    """A batch proposal. `by_name` maps every name given to the brand to use (an existing one
    or a new pending one); `refused` lists names that match a brand this vendor cannot use (a
    rejected brand, another vendor's pending proposal)."""

    batch: BrandBatch | None
    created: list[Brand]
    by_name: dict[str, Brand]
    refused: list[str]
    changes: ChangeSet
    existing: list[Brand] = field(default_factory=list)


# --- Reading


def pending_limit() -> int:
    return int(settings.MPMV_BRAND_PENDING_LIMIT)


def brand_choices(vendor: Vendor) -> "QuerySet[Brand]":
    """The brands `vendor` may put on a product: every approved brand and its own proposals
    that wait for review. There is no cap on how many brands a vendor sells."""
    own = Q(status=Brand.Status.PENDING, proposed_by=vendor)
    return Brand.objects.filter(Q(status=Brand.Status.APPROVED) | own).order_by("name")


def pending_proposal_count(vendor: Vendor) -> int:
    """The manual proposals of `vendor` still waiting (batch proposals are not counted)."""
    return Brand.objects.filter(
        proposed_by=vendor, status=Brand.Status.PENDING, batch__isnull=True
    ).count()


def brand_snapshot(brand: Brand) -> dict[str, Any]:
    proposer = brand.proposed_by
    return {
        "name": brand.name,
        "status": brand.status,
        "wikidata_id": brand.wikidata_id,
        "url": brand.url,
        "same_as": list(brand.same_as),
        "logo_alt": brand.logo_alt,
        "manufacturer": brand.manufacturer_id,
        # Named `vendor` so the entry shows in the proposing vendor's audit trail.
        "vendor": None if proposer is None else str(proposer.public_id),
        "batch": None if brand.batch is None else str(brand.batch.public_id),
    }


def batch_snapshot(batch: BrandBatch, names: Sequence[str] = ()) -> dict[str, Any]:
    data: dict[str, Any] = {
        "source": batch.source,
        "status": batch.status,
        "vendor": str(batch.vendor.public_id),
    }
    if names:
        data["brands"] = list(names)
    return data


def manufacturer_snapshot(maker: Manufacturer) -> dict[str, Any]:
    return {
        "legal_name": maker.legal_name,
        "url": maker.url,
        "same_as": list(maker.same_as),
        "country": maker.country,
    }


# --- Proposals


def _candidate(
    name: str,
    *,
    vendor: Vendor,
    batch: BrandBatch | None = None,
    wikidata_id: str = "",
    url: str = "",
    same_as: Iterable[str] = (),
) -> Brand:
    """A new pending brand, validated (duplicates by name key and Wikidata ID, https links)."""
    brand = Brand(
        name=name.strip(),
        status=Brand.Status.PENDING,
        proposed_by=vendor,
        batch=batch,
        wikidata_id=wikidata_id.strip(),
        url=url.strip(),
        same_as=[link.strip() for link in same_as],
    )
    brand.full_clean()
    return brand


def propose_brand(
    vendor: Vendor,
    name: str,
    *,
    ctx: AuditContext,
    wikidata_id: str = "",
    url: str = "",
    same_as: Iterable[str] = (),
    dry_run: bool = False,
) -> BrandOutcome:
    """A vendor proposes a brand that is missing from the list. It stays pending, visible to
    this vendor only, until staff review it. Refused with a message when the name or Wikidata ID
    is already a brand, a link is not https, or the vendor already has `pending_limit()` manual
    proposals waiting."""
    with transaction.atomic():
        # Lock the vendor row so two requests cannot both take the last slot.
        Vendor.objects.select_for_update().get(pk=vendor.pk)
        limit = pending_limit()
        if pending_proposal_count(vendor) >= limit:
            raise ValidationError(
                {
                    "name": (
                        f"You already have {limit} brand proposals waiting for review. "
                        "Wait for a decision, or pick an existing brand, before proposing "
                        "another."
                    )
                }
            )
        brand = _candidate(name, vendor=vendor, wikidata_id=wikidata_id, url=url, same_as=same_as)
        changes = ChangeSet()
        changes.create(BRAND_MODEL, str(brand.public_id), brand_snapshot(brand))
        if dry_run:
            return BrandOutcome(None, changes)
        brand.save()
        audit_changes(changes, ctx=ctx, action=BRAND_PROPOSED)
    return BrandOutcome(brand, changes)


def propose_batch(
    vendor: Vendor,
    names: Iterable[str],
    *,
    source: str,
    ctx: AuditContext,
    dry_run: bool = False,
) -> BatchOutcome:
    """The brands one feed or import run brought: known ones are reused, the new ones become one
    pending batch (reviewed together, outside the manual-proposal limit). No batch is made when
    nothing is new."""
    chosen: dict[str, Brand] = {}
    by_key: dict[str, Brand] = {}
    created: list[Brand] = []
    existing: list[Brand] = []
    refused: list[str] = []
    usable = {brand.name_key: brand for brand in brand_choices(vendor)}
    taken = set(Brand.objects.values_list("name_key", flat=True))
    batch = BrandBatch(vendor=vendor, source=source.strip()[:200])
    for raw in names:
        name = raw.strip()
        key = engine.brand_name_key(name)
        if not key:
            refused.append(raw)
            continue
        if key in by_key:
            chosen[raw] = by_key[key]
        elif key in usable:
            by_key[key] = chosen[raw] = usable[key]
            existing.append(usable[key])
        elif key in taken:
            refused.append(raw)
        else:
            brand = _candidate(name, vendor=vendor, batch=batch)
            by_key[key] = chosen[raw] = brand
            created.append(brand)
    changes = ChangeSet()
    if not created:
        return BatchOutcome(None, [], chosen, refused, changes, existing)
    names_made = [brand.name for brand in created]
    batch_changes = ChangeSet()
    batch_changes.create(BATCH_MODEL, str(batch.public_id), batch_snapshot(batch, names_made))
    changes.changes.extend(batch_changes.changes)
    for brand in created:
        changes.create(BRAND_MODEL, str(brand.public_id), brand_snapshot(brand))
    if dry_run:
        return BatchOutcome(None, created, chosen, refused, changes, existing)
    with transaction.atomic():
        batch.save()
        for brand in created:
            brand.batch = batch
            brand.save()
        audit_changes(batch_changes, ctx=ctx, action=BATCH_PROPOSED)
    return BatchOutcome(batch, created, chosen, refused, changes, existing)


# --- Review


def _pending_only(brand: Brand) -> None:
    if brand.status != Brand.Status.PENDING:
        raise ValidationError("Only a brand waiting for review can be approved or rejected.")


def _review(
    brand: Brand, status: Brand.Status, action: str, *, ctx: AuditContext, dry_run: bool
) -> ChangeSet:
    _pending_only(brand)
    changes = ChangeSet()
    before = brand_snapshot(brand)
    after = {**before, "status": status}
    if status == Brand.Status.REJECTED:
        # A rejected brand is never shown: the products that carried it lose the link.
        after["products_released"] = Product.objects.filter(brand=brand).count()
    changes.update(BRAND_MODEL, str(brand.public_id), before, after)

    def apply() -> None:
        stored = Brand.objects.select_for_update().get(pk=brand.pk)
        _pending_only(stored)
        if status == Brand.Status.REJECTED:
            Product.objects.filter(brand=brand).update(brand=None)
        brand.status = status
        brand.save(update_fields=["status", "updated_at"])

    return run_mutation(changes, apply, ctx=ctx, action=action, dry_run=dry_run)


def approve_brand(brand: Brand, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Make a proposed brand public (it frees one of the vendor's proposal slots)."""
    return _review(brand, Brand.Status.APPROVED, BRAND_APPROVED, ctx=ctx, dry_run=dry_run)


def reject_brand(brand: Brand, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Refuse a proposed brand: the products using it lose it and the slot is freed. The
    vendor can pick an existing brand or propose it again with better data."""
    return _review(brand, Brand.Status.REJECTED, BRAND_REJECTED, ctx=ctx, dry_run=dry_run)


def _review_batch(
    batch: BrandBatch,
    status: Brand.Status,
    batch_action: str,
    brand_action: str,
    *,
    ctx: AuditContext,
    dry_run: bool,
) -> ChangeSet:
    if batch.status != BrandBatch.Status.PENDING:
        raise ValidationError("This batch has already been reviewed.")
    pending = list(batch.brands.filter(status=Brand.Status.PENDING).select_related("proposed_by"))
    batch_changes = ChangeSet()
    before = batch_snapshot(batch)
    batch_changes.update(BATCH_MODEL, str(batch.public_id), before, {**before, "status": status})
    brand_changes = ChangeSet()
    for brand in pending:
        snap = brand_snapshot(brand)
        after = {**snap, "status": status}
        if status == Brand.Status.REJECTED:
            after["products_released"] = Product.objects.filter(brand=brand).count()
        brand_changes.update(BRAND_MODEL, str(brand.public_id), snap, after)
    result = ChangeSet([*batch_changes.changes, *brand_changes.changes])
    if dry_run:
        return result
    with transaction.atomic():
        stored = BrandBatch.objects.select_for_update().get(pk=batch.pk)
        if stored.status != BrandBatch.Status.PENDING:
            raise ValidationError("This batch has already been reviewed.")
        if status == Brand.Status.REJECTED:
            Product.objects.filter(brand__in=pending).update(brand=None)
        Brand.objects.filter(pk__in=[brand.pk for brand in pending]).update(status=status)
        BrandBatch.objects.filter(pk=batch.pk).update(status=status)
        batch.status = status
        audit_changes(batch_changes, ctx=ctx, action=batch_action)
        audit_changes(brand_changes, ctx=ctx, action=brand_action)
    return result


def approve_batch(batch: BrandBatch, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Approve every pending brand of an import run in one step."""
    return _review_batch(
        batch, Brand.Status.APPROVED, BATCH_APPROVED, BRAND_APPROVED, ctx=ctx, dry_run=dry_run
    )


def reject_batch(batch: BrandBatch, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Reject every pending brand of an import run in one step."""
    return _review_batch(
        batch, Brand.Status.REJECTED, BATCH_REJECTED, BRAND_REJECTED, ctx=ctx, dry_run=dry_run
    )


# --- Merge


def _merge_redirects(source: Brand, target: Brand) -> list[Redirect]:
    """The 301 rules that keep the old brand page of every language alive, and the earlier rules
    that pointed at it (so they stay one hop). A brand that never had a public page needs none."""
    if source.status != Brand.Status.APPROVED:
        return []
    old, new = services.brand_page_paths(source), services.brand_page_paths(target)
    fallback = new.get(translations.source_language()) or next(iter(new.values()), None)
    rules: list[Redirect] = []
    for language, old_path in old.items():
        new_path = new.get(language) or fallback
        if new_path is None or new_path == old_path:
            continue
        rule = Redirect.objects.filter(source_path=old_path).first() or Redirect(
            source_path=old_path
        )
        rule.code = Redirect.Code.MOVED
        rule.target_kind = Redirect.Target.PATH
        rule.target_path = new_path
        rule.target_product = None
        rule.target_term = None
        rule.target_query = ""
        rule.match_prefix = False
        rule.origin = Redirect.Origin.MERGED_BRAND
        rule.expires_at = None
        rules.append(rule)
        for earlier in Redirect.objects.filter(
            target_kind=Redirect.Target.PATH, target_path=old_path
        ).exclude(source_path=old_path):
            earlier.target_path = new_path
            rules.append(earlier)
    return rules


def merge_brands(
    source: Brand, target: Brand, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Merge `source` into the approved `target`: its products move, the brand is removed and its
    old page redirects (301, kept through the catalog redirect rules) to the target's page."""
    if source.pk == target.pk:
        raise ValidationError("Choose a different brand to merge into.")
    if target.status != Brand.Status.APPROVED:
        raise ValidationError("A brand can only be merged into an approved brand.")
    rules = _merge_redirects(source, target)
    for rule in rules:
        rule.full_clean()
    problems = services.redirect_problems(rules)
    if problems:
        raise ValidationError({NON_FIELD_ERRORS: list(problems.values())})

    moved = Product.objects.filter(brand=source).count()
    brand_changes = ChangeSet()
    brand_changes.delete(BRAND_MODEL, str(source.public_id), brand_snapshot(source))
    product_count = Product.objects.filter(brand=target).count()
    brand_changes.update(
        BRAND_MODEL,
        str(target.public_id),
        {**brand_snapshot(target), "products": product_count},
        {**brand_snapshot(target), "products": product_count + moved, "merged_from": source.name},
    )
    created, updated = ChangeSet(), ChangeSet()
    for rule in rules:
        after = services.redirect_snapshot(rule)
        if rule.pk is None:
            created.create(services.REDIRECT_MODEL, rule.source_path, after)
        else:
            stored = Redirect.objects.get(pk=rule.pk)
            updated.update(
                services.REDIRECT_MODEL,
                rule.source_path,
                services.redirect_snapshot(stored),
                after,
            )
    result = ChangeSet([*brand_changes.changes, *created.changes, *updated.changes])
    if dry_run:
        return result
    with transaction.atomic():
        Brand.objects.select_for_update().filter(pk__in=[source.pk, target.pk]).count()
        Product.objects.filter(brand=source).update(brand=target)
        for rule in rules:
            rule.save()
        source.delete()
        audit_changes(brand_changes, ctx=ctx, action=BRAND_MERGED)
        audit_changes(created, ctx=ctx, action=services.REDIRECT_CREATED)
        audit_changes(updated, ctx=ctx, action=services.REDIRECT_UPDATED)
    return result


# --- Staff edits (the admin writes through these)


def save_brand(brand: Brand, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Create or change a brand (validated: name and Wikidata duplicates, https links)."""
    brand.full_clean()
    return save_audited(
        brand,
        brand_snapshot,
        key=str(brand.public_id),
        ctx=ctx,
        created=BRAND_CREATED,
        updated=BRAND_UPDATED,
        dry_run=dry_run,
    )


def delete_brand(brand: Brand, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Delete a brand nobody uses; a brand with products is merged into another instead."""
    if Product.objects.filter(brand=brand).exists():
        raise ValidationError("This brand has products: merge it into another brand instead.")
    changes = ChangeSet()
    changes.delete(BRAND_MODEL, str(brand.public_id), brand_snapshot(brand))
    return run_mutation(changes, brand.delete, ctx=ctx, action=BRAND_DELETED, dry_run=dry_run)


def save_manufacturer(
    maker: Manufacturer, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    maker.full_clean()
    return save_audited(
        maker,
        manufacturer_snapshot,
        key=str(maker.public_id),
        ctx=ctx,
        created=MANUFACTURER_CREATED,
        updated=MANUFACTURER_UPDATED,
        dry_run=dry_run,
    )


def delete_manufacturer(
    maker: Manufacturer, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    changes = ChangeSet()
    changes.delete(MANUFACTURER_MODEL, str(maker.public_id), manufacturer_snapshot(maker))
    return run_mutation(
        changes, maker.delete, ctx=ctx, action=MANUFACTURER_DELETED, dry_run=dry_run
    )
