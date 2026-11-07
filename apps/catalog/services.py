"""Catalog services: audited price changes (CAT.1), taxonomy queries (CAT.2) and the
catalogue integrity rules (CAT.14).

Every price change goes through here, whatever the adapter (admin, API, import, MCP): it is
validated, returned as a ChangeSet (dry-run) and written to the audit log. The 30-day price
history for the Omnibus rule (CMP.6) can be built from these audit rows.
"""

import datetime as dt
from collections.abc import Iterable
from typing import Any

from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django.db.models import Q, QuerySet

from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.services import run_mutation
from apps.vendors.models import Vendor
from apps.vendors.services import vendors_in_suggestions

from . import engine
from .models import Category, Product, ProductStatus, StockStatus, Term, Variant

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

    `price_net=None` clears the price, allowed only when the product is not for sale (Q-31).

    The change is computed against the stored row (locked), never a possibly stale instance.
    Raises ValidationError for negative, sub-cent or inconsistent prices and TypeError for
    floats, also on dry-run.
    """
    prices = {
        "price_net": _price("price_net", price_net, optional=True),
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
        missing = engine.price_required_error(current.sales_mode, prices["price_net"])
        if missing is not None:
            raise ValidationError({"price_net": missing})
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


# --- Catalogue integrity (CAT.14, owner decisions Q-32 and Q-33)

PRODUCT_DUPLICATED = "catalog.product.duplicated_to_vendor"
UPSELL_NOT_ALLOWED = "{sku}: its vendor has not opted in to other vendors' suggestions."

# A copy gets its own identity and timestamps.
DUPLICATE_SKIPPED = frozenset({"id", "public_id", "created_at", "updated_at"})


def _copy[M: models.Model](instance: M, **overrides: Any) -> M:
    """An unsaved copy of `instance`'s concrete fields (by attname), with `overrides`."""
    model = type(instance)
    values = {
        field.attname: getattr(instance, field.attname)
        for field in model._meta.concrete_fields
        if field.name not in DUPLICATE_SKIPPED
    }
    values.update(overrides)
    return model(**values)


def duplicate_product_to_vendor(
    product: Product,
    vendor: Vendor,
    *,
    actor: AbstractBaseUser | AnonymousUser,
    reason: str,
    ctx: AuditContext,
    dry_run: bool = False,
) -> ChangeSet:
    """Copy `product` as a draft of another vendor (staff only, audited; Q-33).

    Products never move between vendors: the original keeps its orders, ledger and history and
    can be retired. The copy gets the texts (without slugs, generated again), variants,
    classification, identifiers and the last price; not the stock (the original vendor's), the
    legacy ids, the required companion or the suggestions (the original vendor's choices).
    Raises PermissionDenied for non-staff and ValidationError for an empty reason, the same
    vendor or a SKU the target vendor already uses.
    """
    if not getattr(actor, "is_staff", False):
        raise PermissionDenied("Only staff can duplicate a product to another vendor.")
    reason = reason.strip()
    if not reason:
        raise ValidationError({"reason": "Say why the product is duplicated."})

    with transaction.atomic():
        original = (
            Product.objects.select_for_update(of=("self",))
            .select_related("vendor")
            .get(pk=product.pk)
        )
        if original.vendor_id == vendor.pk:
            raise ValidationError({"vendor": "Choose another vendor: this one owns the product."})
        copy = _copy(
            original,
            vendor_id=vendor.pk,
            status=ProductStatus.DRAFT,
            stock_qty=0,
            stock_status=StockStatus.OUT_OF_STOCK,
            required_companion_id=None,
            legacy_wp_id_en=None,
            legacy_wp_id_it=None,
        )
        copy.full_clean()
        variants = list(original.variants.prefetch_related("translations"))
        taken = Variant.objects.filter(vendor=vendor, sku__in=[v.sku for v in variants])
        if taken.exists():
            skus = ", ".join(sorted(taken.values_list("sku", flat=True)))
            raise ValidationError({"sku": f"The vendor already has variants with SKU {skus}."})

        changes = ChangeSet()
        changes.create(
            PRODUCT_MODEL,
            str(copy.public_id),
            {
                "sku": copy.sku,
                "vendor": str(vendor.public_id),
                "duplicated_from": str(original.public_id),
                "from_vendor": str(original.vendor.public_id),
                "reason": reason,
            },
        )
        copies = [_copy(variant, vendor_id=vendor.pk, stock_qty=0) for variant in variants]
        for variant, variant_copy in zip(variants, copies, strict=True):
            changes.create(
                VARIANT_MODEL,
                str(variant_copy.public_id),
                {
                    "sku": variant_copy.sku,
                    "product": str(copy.public_id),
                    "duplicated_from": str(variant.public_id),
                },
            )

        def apply() -> None:
            copy.save()
            copy.secondary_categories.set(original.secondary_categories.all())
            copy.terms.set(original.terms.all())
            for translation in original.translations.all():
                _copy(translation, product_id=copy.pk, slug="").save()
            for variant, variant_copy in zip(variants, copies, strict=True):
                variant_copy.product = copy
                variant_copy.save()
                for text in variant.translations.all():
                    _copy(text, variant_id=variant_copy.pk).save()

        return run_mutation(changes, apply, ctx=ctx, action=PRODUCT_DUPLICATED, dry_run=dry_run)


def check_upsells(source_vendors: set[int], targets: Iterable[int]) -> None:
    """Refuse suggestions by products of the `source_vendors` (ids) to the `targets` (product
    ids) when a target belongs to another vendor that has not opted in to other vendors'
    suggestions (Q-32)."""
    candidates = Product.objects.filter(pk__in=list(targets))
    if len(source_vendors) == 1:  # several source vendors: every target is someone else's
        candidates = candidates.exclude(vendor_id__in=source_vendors)
    refused = candidates.exclude(vendor__in=vendors_in_suggestions())
    skus = list(refused.values_list("sku", flat=True))
    if skus:
        raise ValidationError([UPSELL_NOT_ALLOWED.format(sku=sku) for sku in skus])


def suggested_upsells(product: Product) -> "QuerySet[Product]":
    """The product's suggestions buyers may see: its vendor's own products, and other vendors'
    products only while those vendors opt in (a withdrawal hides them at once)."""
    return product.upsells.filter(
        Q(vendor_id=product.vendor_id) | Q(vendor__in=vendors_in_suggestions())
    )
