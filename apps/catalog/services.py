"""Catalog services: audited price changes (CAT.1), taxonomy queries (CAT.2) and the vendor
SKU: audited renames and matching by match key and alias history (CAT.13).

Every price change goes through here, whatever the adapter (admin, API, import, MCP): it is
validated, returned as a ChangeSet (dry-run) and written to the audit log. The 30-day price
history for the Omnibus rule (CMP.6) can be built from these audit rows.
"""

import datetime as dt
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, QuerySet

from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.services import run_mutation
from apps.vendors.models import Vendor

from . import engine
from .models import (
    Category,
    Product,
    SkuAlias,
    Term,
    Variant,
    sku_owner,
    sku_taken_message,
)

PRODUCT_MODEL = "catalog.product"
VARIANT_MODEL = "catalog.variant"
PRODUCT_PRICE_CHANGED = "catalog.product.price_changed"
VARIANT_PRICE_CHANGED = "catalog.variant.price_changed"

PRODUCT_PRICE_FIELDS = ("price_net", "sale_price_net", "sale_from", "sale_to")
VARIANT_PRICE_FIELDS = ("price_net_override",)


def _price(field: str, value: object, *, optional: bool = False) -> Any:
    if value is None and optional:
        return None
    try:
        return engine.to_price(value)
    except ValueError as exc:
        raise ValidationError({field: str(exc)}) from exc


def product_price_snapshot(product: Product) -> dict[str, Any]:
    """The SKU and vendor make audit rows readable; the public id is the audit key."""
    return {
        "sku": product.sku,
        "vendor": str(product.vendor.public_id),
        **{name: getattr(product, name) for name in PRODUCT_PRICE_FIELDS},
    }


def variant_price_snapshot(variant: Variant) -> dict[str, Any]:
    return {
        "sku": variant.sku,
        "product": str(variant.product.public_id),
        **{name: getattr(variant, name) for name in VARIANT_PRICE_FIELDS},
    }


def set_product_price(
    product: Product,
    *,
    price_net: object,
    sale_price_net: object = None,
    sale_from: dt.datetime | None = None,
    sale_to: dt.datetime | None = None,
    ctx: AuditContext,
    dry_run: bool = False,
) -> ChangeSet:
    """Set the net price and the optional sale of `product`; no sale given ends the current one.

    The change is computed against the stored row (locked), never a possibly stale instance.
    Raises ValidationError for negative, sub-cent or inconsistent prices and TypeError for
    floats, also on dry-run.
    """
    prices = {
        "price_net": _price("price_net", price_net),
        "sale_price_net": _price("sale_price_net", sale_price_net, optional=True),
        "sale_from": sale_from,
        "sale_to": sale_to,
    }
    errors = engine.price_errors(**prices)
    if errors:
        raise ValidationError(errors)

    with transaction.atomic():
        current = (
            Product.objects.select_for_update(of=("self",))
            .select_related("vendor")
            .get(pk=product.pk)
        )
        before = product_price_snapshot(current)
        changes = ChangeSet()
        changes.update(PRODUCT_MODEL, str(current.public_id), before, {**before, **prices})

        def apply() -> None:
            for name, value in prices.items():
                setattr(current, name, value)
            current.save(update_fields=[*PRODUCT_PRICE_FIELDS, "updated_at"])

        run_mutation(changes, apply, ctx=ctx, action=PRODUCT_PRICE_CHANGED, dry_run=dry_run)

    if not dry_run:
        for name, value in prices.items():
            setattr(product, name, value)
    return changes


def set_variant_price(
    variant: Variant,
    *,
    price_net_override: object,
    ctx: AuditContext,
    dry_run: bool = False,
) -> ChangeSet:
    """Set or clear (None) the net price override of `variant`; audited like product prices."""
    prices = {"price_net_override": _price("price_net_override", price_net_override, optional=True)}
    with transaction.atomic():
        current = (
            Variant.objects.select_for_update(of=("self",))
            .select_related("product")
            .get(pk=variant.pk)
        )
        before = variant_price_snapshot(current)
        changes = ChangeSet()
        changes.update(VARIANT_MODEL, str(current.public_id), before, {**before, **prices})

        def apply() -> None:
            current.price_net_override = prices["price_net_override"]
            current.save(update_fields=[*VARIANT_PRICE_FIELDS, "updated_at"])

        run_mutation(changes, apply, ctx=ctx, action=VARIANT_PRICE_CHANGED, dry_run=dry_run)

    if not dry_run:
        variant.price_net_override = prices["price_net_override"]
    return changes


# --- Taxonomies (CAT.2): a lower level implies the upper ones (spec 03 §3.2).


def effective_terms(product: Product) -> "QuerySet[Term]":
    """The product's terms with all their ancestors: tagging a model implies its maker."""
    paths: set[str] = set()
    for path in product.terms.values_list("path", flat=True):
        paths.update((path, *engine.ancestor_paths(path, Term.steplen)))
    return Term.objects.filter(path__in=paths)


def products_with_term(term: Term) -> "QuerySet[Product]":
    """Products tagged with `term` or any term below it."""
    return Product.objects.filter(terms__path__startswith=term.path).distinct()


def products_in_category(
    category: Category, *, include_secondary: bool = True
) -> "QuerySet[Product]":
    """Products whose main category, or optionally a secondary one, is in `category`'s subtree."""
    in_tree = Q(category__path__startswith=category.path)
    if include_secondary:
        in_tree |= Q(secondary_categories__path__startswith=category.path)
    return Product.objects.filter(in_tree).distinct()


# --- The vendor SKU (CAT.13, owner decisions Q-30 and Q-33)

SKU_ALIAS_MODEL = "catalog.skualias"
PRODUCT_SKU_RENAMED = "catalog.product.sku_renamed"
VARIANT_SKU_RENAMED = "catalog.variant.sku_renamed"
SKU_ROWS_UPDATED = "catalog.sku_rows_updated"

# Fields an update by SKU may write: stock only. Prices, weights and the rest go through their
# own audited services; creating products belongs to the import pipelines (ING).
SKU_ROW_FIELDS = frozenset({"stock_qty", "stock_status"})

type SkuItem = Product | Variant


def _label(item: SkuItem) -> str:
    """How a report names an item to the vendor: never the staff-only `staff_ref`."""
    kind = "product" if isinstance(item, Product) else "variant"
    return f"{kind} {item.sku!r} ({item.public_id})"


class AmbiguousSkuMatch(ValidationError):
    """Some SKUs match more than one item of the vendor (a current SKU and another item's old
    alias): the run stops, the importer never guesses. `candidates` maps each such SKU, as
    given, to the items it matches."""

    def __init__(self, candidates: Mapping[str, Sequence[SkuItem]]) -> None:
        self.candidates = {raw: list(items) for raw, items in candidates.items()}
        lines = [
            f"SKU {raw!r} matches " + " and ".join(_label(item) for item in items)
            for raw, items in self.candidates.items()
        ]
        super().__init__("Ambiguous SKU match, nothing written: " + "; ".join(lines) + ".")


def sku_snapshot(item: SkuItem) -> dict[str, Any]:
    return {"sku": item.sku, "sku_key": item.sku_key, "vendor": str(item.vendor.public_id)}


def _lock(item: SkuItem) -> SkuItem:
    if isinstance(item, Product):
        return (
            Product.objects.select_for_update(of=("self",)).select_related("vendor").get(pk=item.pk)
        )
    return Variant.objects.select_for_update(of=("self",)).select_related("vendor").get(pk=item.pk)


def _rename(
    item: SkuItem, sku: str, *, model: str, action: str, ctx: AuditContext, dry_run: bool
) -> ChangeSet:
    errors = engine.sku_errors(sku)
    if errors:
        raise ValidationError({"sku": errors})
    new_key = engine.sku_match_key(sku)
    with transaction.atomic():
        current = _lock(item)
        owner = sku_owner(
            current.vendor_id,
            new_key,
            product_pk=current.pk if isinstance(current, Product) else None,
            variant_pk=current.pk if isinstance(current, Variant) else None,
        )
        if owner is not None:
            raise ValidationError({"sku": sku_taken_message(owner)})
        before = sku_snapshot(current)
        changes = ChangeSet()
        changes.update(
            model, str(current.public_id), before, {**before, "sku": sku, "sku_key": new_key}
        )
        if new_key != current.sku_key:  # the database trigger writes the alias row
            changes.create(
                SKU_ALIAS_MODEL,
                f"{current.vendor.public_id}:{current.sku_key}",
                {
                    "sku": current.sku,
                    "sku_key": current.sku_key,
                    model.split(".")[1]: str(current.public_id),
                },
            )

        def apply() -> None:
            current.sku = sku
            current.save(update_fields=["sku", "updated_at"])

        run_mutation(changes, apply, ctx=ctx, action=action, dry_run=dry_run)

    if not dry_run:
        item.sku, item.sku_key = sku, new_key
    return changes


def rename_product_sku(
    product: Product, sku: str, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Give `product` a new vendor SKU, kept raw. The old match key stays as a `SkuAlias`, so
    older import files still match. Odd characters are allowed (`engine.sku_warnings`); an
    empty SKU or a match key the vendor already uses is a ValidationError."""
    return _rename(
        product, sku, model=PRODUCT_MODEL, action=PRODUCT_SKU_RENAMED, ctx=ctx, dry_run=dry_run
    )


def rename_variant_sku(
    variant: Variant, sku: str, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """`rename_product_sku` for a variant: the namespace is shared with products."""
    return _rename(
        variant, sku, model=VARIANT_MODEL, action=VARIANT_SKU_RENAMED, ctx=ctx, dry_run=dry_run
    )


def match_skus(vendor: Vendor, skus: Iterable[str]) -> dict[str, SkuItem | None]:
    """The product or variant of `vendor` each SKU (as given) refers to, or None.

    Matches the current match key, then the `SkuAlias` history. A SKU matching more than one
    item raises AmbiguousSkuMatch naming every candidate, before anything is written.
    """
    raw_skus = list(dict.fromkeys(skus))
    keys = {raw: engine.sku_match_key(raw) for raw in raw_skus}
    wanted = set(keys.values())
    found: dict[str, dict[tuple[str, int], SkuItem]] = {key: {} for key in wanted}
    for product in Product.objects.filter(vendor=vendor, sku_key__in=wanted):
        found[product.sku_key][("product", product.pk)] = product
    for variant in Variant.objects.filter(vendor=vendor, sku_key__in=wanted):
        found[variant.sku_key][("variant", variant.pk)] = variant
    aliases = SkuAlias.objects.filter(vendor=vendor, sku_key__in=wanted).select_related(
        "product", "variant"
    )
    for alias in aliases:
        if alias.product is not None:
            found[alias.sku_key].setdefault(("product", alias.product.pk), alias.product)
        elif alias.variant is not None:
            found[alias.sku_key].setdefault(("variant", alias.variant.pk), alias.variant)

    result: dict[str, SkuItem | None] = {}
    ambiguous: dict[str, list[SkuItem]] = {}
    for raw in raw_skus:
        items = list(found[keys[raw]].values())
        if len(items) > 1:
            ambiguous[raw] = items
        result[raw] = items[0] if len(items) == 1 else None
    if ambiguous:
        raise AmbiguousSkuMatch(ambiguous)
    return result


@dataclass(frozen=True)
class SkuRow:
    """One row of a vendor file: a SKU and the values to set (`SKU_ROW_FIELDS` only)."""

    sku: str
    values: Mapping[str, Any] = field(default_factory=dict)


def update_by_sku(
    vendor: Vendor, rows: Sequence[SkuRow], *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Update the vendor's products and variants named by SKU (match key, then aliases).

    All or nothing: an ambiguous SKU (AmbiguousSkuMatch), an unknown SKU or a field outside
    `SKU_ROW_FIELDS` (ValidationError) stops the run before anything is written. Another
    vendor's items are never matched.
    """
    outside = sorted({name for row in rows for name in row.values} - SKU_ROW_FIELDS)
    if outside:
        raise ValidationError(f"These fields cannot be updated by SKU: {', '.join(outside)}.")
    matches = match_skus(vendor, (row.sku for row in rows))
    unknown = [raw for raw, item in matches.items() if item is None]
    if unknown:
        raise ValidationError(
            "No product or variant of this vendor has these SKUs: "
            + ", ".join(repr(raw) for raw in unknown)
            + "."
        )

    changes = ChangeSet()
    updates: list[tuple[SkuItem, dict[str, Any]]] = []
    for row in rows:
        item = matches[row.sku]
        if item is None:  # pragma: no cover - refused above
            continue
        before = {"sku": item.sku, **{name: getattr(item, name) for name in row.values}}
        model = PRODUCT_MODEL if isinstance(item, Product) else VARIANT_MODEL
        changes.update(model, str(item.public_id), before, {**before, **row.values})
        updates.append((item, dict(row.values)))

    def apply() -> None:
        for item, values in updates:
            for name, value in values.items():
                setattr(item, name, value)
            item.save(update_fields=[*values, "updated_at"])

    return run_mutation(changes, apply, ctx=ctx, action=SKU_ROWS_UPDATED, dry_run=dry_run)
