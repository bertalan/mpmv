"""Brand proposals, review and merge (CAT.20 2/3).

Brands are one shared list. A vendor picks any approved brand (and its own pending proposals) and
proposes a missing one; Catalog staff approve, reject or merge it into an existing brand. Brands
that arrive with a feed or import are proposed as one batch per run, outside the per-vendor limit
on manual proposals (`settings.MPMV_BRAND_PENDING_LIMIT`, default 20).

Every mutation takes `dry_run` and returns a `ChangeSet`, validates before it writes, and audits
in the same transaction. Staff edits of brands and manufacturers (`save_*`, `delete_*`) are
audited too; the admin calls these.
"""

import logging
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import Q, QuerySet

from apps.core import translations
from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.services import audit_changes, enqueue_email, run_mutation, save_audited
from apps.vendors.models import Vendor, VendorMember

from . import engine, services
from .models import Brand, BrandBatch, Manufacturer, Product, Redirect

logger = logging.getLogger(__name__)

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
    or a new pending one, which may reopen a rejected brand); `refused` lists names that match
    a brand this vendor cannot use (another vendor's pending proposal)."""

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


DUPLICATE_MESSAGE = (
    "A brand with this name or Wikidata ID already exists, or was proposed a moment ago. "
    "Pick it from the list instead."
)


BRAND_NAMES_LOCK = 0x42524E44  # one advisory lock key for every change to the brand names


def lock_brand_names() -> None:
    """Serialise changes to the shared brand names until the transaction ends. The unique
    constraints cover pending against pending; this covers approved against pending (a
    proposal racing a staff brand, an approval racing a proposal)."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [BRAND_NAMES_LOCK])


def _refuse_taken_keys(brands_: Iterable[Brand]) -> None:
    """Refuse when a brand's normalised name belongs to another approved or pending brand
    outside `brands_` (call under `lock_brand_names`)."""
    chosen = list(brands_)
    taken = (
        Brand.objects.exclude(pk__in=[brand.pk for brand in chosen])
        .exclude(status=Brand.Status.REJECTED)
        .filter(name_key__in=[brand.name_key for brand in chosen if brand.name_key])
        .exists()
    )
    if taken:
        raise ValidationError({"name": DUPLICATE_MESSAGE})


@contextmanager
def _duplicate_guard() -> Iterator[None]:
    """Turn a brand uniqueness violation (the database has the last word when two requests race
    past the checks) into the same refusal as a duplicate found by validation. Savepoint, so the
    caller's transaction stays usable."""
    try:
        with transaction.atomic():
            yield
    except IntegrityError as error:
        if "catalog_brand" not in str(error):
            raise
        raise ValidationError({"name": DUPLICATE_MESSAGE}) from error


def _candidate(
    name: str,
    *,
    vendor: Vendor,
    batch: BrandBatch | None = None,
    wikidata_id: str = "",
    url: str = "",
    same_as: Iterable[str] = (),
) -> tuple[Brand, dict[str, Any] | None]:
    """A pending brand, validated (duplicates by name key and Wikidata ID, https links), and
    its snapshot before when it reopens a rejected brand of the same name or Wikidata ID (the
    rejection notice invites the vendor to propose again; the rejected row keeps its name and
    Wikidata ID reserved, so it is reused as the new proposal). Unsaved: the caller saves."""
    name = name.strip()
    wikidata = wikidata_id.strip()
    rejected = Brand.objects.select_for_update().filter(status=Brand.Status.REJECTED)
    reopened = rejected.filter(name_key=engine.brand_name_key(name)).first()
    if reopened is None and wikidata:
        reopened = rejected.filter(wikidata_id=wikidata).first()
    before = None if reopened is None else brand_snapshot(reopened)
    brand = reopened or Brand()
    brand.name = name
    brand.status = Brand.Status.PENDING
    brand.proposed_by = vendor
    brand.batch = batch
    brand.wikidata_id = wikidata
    brand.url = url.strip()
    brand.same_as = [link.strip() for link in same_as]
    brand.full_clean()
    return brand, before


def _record_proposal(changes: ChangeSet, brand: Brand, before: dict[str, Any] | None) -> None:
    after = brand_snapshot(brand)
    if before is None:
        changes.create(BRAND_MODEL, str(brand.public_id), after)
    else:
        changes.update(BRAND_MODEL, str(brand.public_id), before, after)


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
        lock_brand_names()
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
        brand, before = _candidate(
            name, vendor=vendor, wikidata_id=wikidata_id, url=url, same_as=same_as
        )
        changes = ChangeSet()
        _record_proposal(changes, brand, before)
        if dry_run:
            return BrandOutcome(None, changes)
        with _duplicate_guard():
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
    batch_limit = int(settings.MPMV_BRAND_BATCH_LIMIT)
    with transaction.atomic():
        lock_brand_names()
        # One batch at a time per vendor: the ceiling below is checked on a stable count.
        Vendor.objects.select_for_update().get(pk=vendor.pk)
        chosen: dict[str, Brand] = {}
        by_key: dict[str, Brand] = {}
        created: list[Brand] = []
        existing: list[Brand] = []
        refused: list[str] = []
        usable = {brand.name_key: brand for brand in brand_choices(vendor)}
        # A rejected name is not taken: proposing it again reopens the rejected brand.
        taken = set(
            Brand.objects.exclude(status=Brand.Status.REJECTED).values_list("name_key", flat=True)
        )
        befores: dict[Any, dict[str, Any] | None] = {}
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
                if len(created) >= batch_limit:
                    raise ValidationError(
                        f"One import can propose at most {batch_limit} new brands. "
                        "Split the file, or ask the platform team to add the brands."
                    )
                brand, before = _candidate(name, vendor=vendor, batch=batch)
                by_key[key] = chosen[raw] = brand
                created.append(brand)
                befores[brand.public_id] = before
        changes = ChangeSet()
        if not created:
            return BatchOutcome(None, [], chosen, refused, changes, existing)
        ceiling = int(settings.MPMV_BRAND_BATCH_PENDING_LIMIT)
        waiting = Brand.objects.filter(
            proposed_by=vendor, status=Brand.Status.PENDING, batch__isnull=False
        ).count()
        if waiting + len(created) > ceiling:
            raise ValidationError(
                f"You already have {waiting} imported brands waiting for review (the limit is "
                f"{ceiling}). Wait for a decision before importing more new brands."
            )
        batch_changes = ChangeSet()
        batch_changes.create(
            BATCH_MODEL, str(batch.public_id), batch_snapshot(batch, [b.name for b in created])
        )
        changes.changes.extend(batch_changes.changes)
        for brand in created:
            _record_proposal(changes, brand, befores[brand.public_id])
        if dry_run:
            return BatchOutcome(None, created, chosen, refused, changes, existing)
        with _duplicate_guard():
            batch.save()
            for brand in created:
                brand.batch = batch
                brand.save()
        audit_changes(batch_changes, ctx=ctx, action=BATCH_PROPOSED)
        return BatchOutcome(batch, created, chosen, refused, changes, existing)


# --- Review


def _products_of(brands_: Iterable[Brand]) -> dict[int, list[str]]:
    """The staff references of the products using each brand (by brand id), for the audit."""
    found: dict[int, list[str]] = {}
    rows = Product.objects.filter(brand__in=list(brands_)).order_by("staff_ref")
    for brand_id, ref in rows.values_list("brand_id", "staff_ref"):
        found.setdefault(brand_id, []).append(ref)
    return found


REJECTED_TEMPLATE = "catalog.brand_rejected"


def _owner_language(vendor: Vendor) -> str:
    """The language of the vendor's owner (a vendor has none of its own yet); unknown or
    unconfigured languages fall back to English when the email is queued."""
    owner = (
        VendorMember.objects.filter(vendor=vendor, role=VendorMember.Role.OWNER)
        .select_related("user")
        .order_by("id")
        .first()
    )
    return owner.user.preferred_language if owner is not None else translations.source_language()


def _product_title(product: Product, language: str) -> str:
    """The product's name in `language`, else the source language, else any; the SKU when it has
    no text yet. Drafts count: the vendor must recognise its own product."""
    texts = {text.language: text.title for text in product.translations.all() if text.title}
    for code in (language, translations.source_language()):
        if code in texts:
            return texts[code]
    return next(iter(texts.values()), product.sku)


def _notify_rejected(rejected: Sequence[tuple[Brand, Sequence[Product]]], *, key: str) -> None:
    """One notice per vendor that lost a brand on its products, naming the rejected brand(s) and
    that vendor's own products (nothing of other vendors), through the outbox in the caller's
    transaction. A refused address is logged and skipped so it never blocks the review."""
    per_vendor: dict[int, tuple[Vendor, list[tuple[Brand, list[Product]]]]] = {}
    for brand, products in rejected:
        # Whoever proposed the brand is told even when no product used it, so it can pick another.
        proposer = brand.proposed_by
        if proposer is not None:
            _, entries = per_vendor.setdefault(proposer.pk, (proposer, []))
            entries.append((brand, []))
        for product in products:
            vendor, entries = per_vendor.setdefault(product.vendor_id, (product.vendor, []))
            if not entries or entries[-1][0].pk != brand.pk:
                entries.append((brand, []))
            entries[-1][1].append(product)
    for vendor, entries in per_vendor.values():
        language = _owner_language(vendor)
        try:
            enqueue_email(
                recipient=vendor.contact_email,
                template_key=REJECTED_TEMPLATE,
                language=language,
                idempotency_key=f"brand-rejected:{key}:{vendor.pk}",
                context={
                    "vendor_name": vendor.display_name,
                    "brands": [
                        {
                            "name": brand.name,
                            "products": [
                                {"title": _product_title(p, language), "sku": p.sku}
                                for p in products
                            ],
                        }
                        for brand, products in entries
                    ],
                },
            )
        except ValueError:
            logger.warning("brand rejection notice not queued for vendor %s", vendor.public_id)


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
        # A rejected brand is never shown: the products that carried it lose the link, and
        # the audit entry names them (staff references) so the link can be restored.
        after["products_released"] = _products_of([brand]).get(brand.pk, [])
    changes.update(BRAND_MODEL, str(brand.public_id), before, after)

    def apply() -> None:
        if status == Brand.Status.APPROVED:
            lock_brand_names()
            _refuse_taken_keys([brand])
        stored = Brand.objects.select_for_update().get(pk=brand.pk)
        _pending_only(stored)
        if status == Brand.Status.REJECTED:
            affected = list(
                Product.objects.filter(brand=brand).select_related("vendor").order_by("pk")
            )
            Product.objects.filter(brand=brand).update(brand=None)
            _notify_rejected([(brand, affected)], key=str(brand.public_id))
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
    released = _products_of(pending) if status == Brand.Status.REJECTED else {}
    for brand in pending:
        snap = brand_snapshot(brand)
        after = {**snap, "status": status}
        if status == Brand.Status.REJECTED:
            after["products_released"] = released.get(brand.pk, [])
        brand_changes.update(BRAND_MODEL, str(brand.public_id), snap, after)
    result = ChangeSet([*batch_changes.changes, *brand_changes.changes])
    if dry_run:
        return result
    with transaction.atomic():
        stored = BrandBatch.objects.select_for_update().get(pk=batch.pk)
        if stored.status != BrandBatch.Status.PENDING:
            raise ValidationError("This batch has already been reviewed.")
        if status == Brand.Status.APPROVED:
            lock_brand_names()
            _refuse_taken_keys(pending)
        if status == Brand.Status.REJECTED:
            used = list(
                Product.objects.filter(brand__in=pending)
                .select_related("vendor")
                .order_by("brand_id", "pk")
            )
            Product.objects.filter(brand__in=pending).update(brand=None)
            _notify_rejected(
                [(b, [p for p in used if p.brand_id == b.pk]) for b in pending],
                key=str(batch.public_id),
            )
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
    with transaction.atomic():
        # Both rows are locked in pk order and read again: another request may have rejected
        # the target, or merged the source, since the caller loaded them.
        locked = {
            brand.pk: brand
            for brand in Brand.objects.select_for_update()
            .filter(pk__in=[source.pk, target.pk])
            .order_by("pk")
        }
        if source.pk not in locked:
            raise ValidationError("This brand no longer exists: it may already have been merged.")
        if target.pk not in locked:
            raise ValidationError("The brand to merge into no longer exists.")
        source, target = locked[source.pk], locked[target.pk]
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
            {
                **brand_snapshot(target),
                "products": product_count + moved,
                "merged_from": source.name,
            },
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
    with transaction.atomic():
        lock_brand_names()
        brand.full_clean()
        with _duplicate_guard():
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
