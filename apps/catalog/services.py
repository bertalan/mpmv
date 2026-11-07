"""Catalog services: audited price changes (CAT.1), taxonomy queries (CAT.2) and product slugs
with their history (CAT.5).

Every price change goes through here, whatever the adapter (admin, API, import, MCP): it is
validated, returned as a ChangeSet (dry-run) and written to the audit log. The 30-day price
history for the Omnibus rule (CMP.6) can be built from these audit rows.
"""

import datetime as dt
from collections.abc import Iterable
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from apps.core.audit import AuditContext, record
from apps.core.changeset import ChangeSet
from apps.core.services import run_mutation

from . import engine, slugs
from .models import Category, Product, ProductTranslation, SlugHistory, Term, Variant

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


# --- Product slugs (CAT.5, spec 03 §3.2): `{title}-{lang}`, never the SKU; old slugs answer 301.

TRANSLATION_MODEL = "catalog.producttranslation"
SLUG_HISTORY_MODEL = "catalog.slughistory"
SLUG_CHANGED = "catalog.product.slug_changed"
OLD_SLUG_RECORDED = "catalog.product.old_slug_recorded"
OLD_SLUG_MAX_LENGTH = 200  # SlugHistory.slug: legacy slugs may be longer than 80 characters


class _SlugsOfOtherProducts:
    """The current and old slugs of the other products in one language, asked one at a time:
    collisions are rare, so a few lookups beat loading every slug of the catalog."""

    def __init__(self, language: str, product_id: int) -> None:
        self.language = language
        self.product_id = product_id

    def __contains__(self, slug: object) -> bool:
        return (
            ProductTranslation.objects.filter(language=self.language, slug=slug)
            .exclude(product_id=self.product_id)
            .exists()
            or SlugHistory.objects.filter(language=self.language, slug=slug)
            .exclude(product_id=self.product_id)
            .exists()
        )


def _slug_snapshot(product: Product, language: str, slug: str) -> dict[str, Any]:
    return {"vendor": str(product.vendor.public_id), "language": language, "slug": slug}


def sync_product_slug(
    translation: ProductTranslation, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Derive the translation's slug from its title; a replaced slug moves to SlugHistory.

    The slug is unique per language among the current and old slugs of every other product, so
    an old slug always redirects to one product in a single hop. Going back to one of the
    product's own old slugs takes it out of the history (no redirect loop). Replacing a slug is
    audited; a first slug is not (no URL changes).
    """
    with transaction.atomic():
        current = (
            ProductTranslation.objects.select_for_update(of=("self",))
            .select_related("product__vendor")
            .get(pk=translation.pk)
        )
        product, language, old_slug = current.product, current.language, current.slug
        new_slug = slugs.unique_product_slug(
            current.title, language, taken=_SlugsOfOtherProducts(language, product.pk)
        )
        changes = ChangeSet()
        if new_slug == old_slug:
            return changes

        changes.update(
            TRANSLATION_MODEL, str(current.public_id), {"slug": old_slug}, {"slug": new_slug}
        )
        reclaimed = SlugHistory.objects.filter(
            product=product, language=language, slug=new_slug
        ).first()
        if reclaimed is not None:
            changes.delete(
                SLUG_HISTORY_MODEL,
                str(reclaimed.public_id),
                _slug_snapshot(product, language, new_slug),
            )
        kept = SlugHistory(product=product, language=language, slug=old_slug) if old_slug else None
        if kept is not None:
            changes.create(
                SLUG_HISTORY_MODEL, str(kept.public_id), _slug_snapshot(product, language, old_slug)
            )
        if dry_run:
            return changes

        if reclaimed is not None:
            reclaimed.delete()
        if kept is not None:
            kept.save()
        # A queryset update: the slug is derived data, not a text of the translation workflow.
        ProductTranslation.objects.filter(pk=current.pk).update(
            slug=new_slug, updated_at=timezone.now()
        )
        if old_slug:
            record(
                ctx,
                SLUG_CHANGED,
                PRODUCT_MODEL,
                str(product.public_id),
                _slug_snapshot(product, language, old_slug),
                _slug_snapshot(product, language, new_slug),
            )
    translation.slug = new_slug
    return changes


def rebuild_product_slugs(
    *,
    ctx: AuditContext,
    dry_run: bool = False,
    translations: "QuerySet[ProductTranslation] | None" = None,
) -> ChangeSet:
    """`sync_product_slug` for every translation (or the given ones), in id order.

    This loads the current `{title}-{lang}-{sku}` slugs as history. A dry run checks each
    translation against the stored slugs only, so it can miss a `-2` that two translations of
    the same run would need.
    """
    queryset = ProductTranslation.objects.all() if translations is None else translations
    changes = ChangeSet()
    for translation in queryset.order_by("pk").iterator():
        changes.changes.extend(sync_product_slug(translation, ctx=ctx, dry_run=dry_run).changes)
    return changes


def record_old_slugs(
    product: Product,
    language: str,
    old_slugs: Iterable[str],
    *,
    ctx: AuditContext,
    dry_run: bool = False,
) -> ChangeSet:
    """Add old slugs of `product` in `language` to SlugHistory (e.g. the legacy old slugs).

    The product's current slug and the slugs already in its history are skipped. A slug that
    is not valid, or that another product uses (current or old), stops the load with a
    ValidationError naming the slugs; nothing is written.
    """
    if language not in dict(settings.LANGUAGES):
        raise ValidationError(f"Unknown language: {language}.")
    wanted = list(dict.fromkeys(old_slugs))
    invalid = [slug for slug in wanted if not slugs.is_slug(slug, max_length=OLD_SLUG_MAX_LENGTH)]
    if invalid:
        raise ValidationError(f"These are not valid slugs: {', '.join(invalid)}.")
    with transaction.atomic():
        product = (
            Product.objects.select_for_update(of=("self",))
            .select_related("vendor")
            .get(pk=product.pk)
        )
        own = {
            *ProductTranslation.objects.filter(product=product, language=language).values_list(
                "slug", flat=True
            ),
            *SlugHistory.objects.filter(product=product, language=language).values_list(
                "slug", flat=True
            ),
        }
        new = [slug for slug in wanted if slug not in own]
        others = {
            *ProductTranslation.objects.filter(language=language, slug__in=new)
            .exclude(product=product)
            .values_list("slug", flat=True),
            *SlugHistory.objects.filter(language=language, slug__in=new)
            .exclude(product=product)
            .values_list("slug", flat=True),
        }
        if others:
            raise ValidationError(
                f"These slugs belong to another product: {', '.join(sorted(others))}."
            )
        entries = [SlugHistory(product=product, language=language, slug=slug) for slug in new]
        changes = ChangeSet()
        for entry in entries:
            changes.create(
                SLUG_HISTORY_MODEL,
                str(entry.public_id),
                _slug_snapshot(product, language, entry.slug),
            )
        run_mutation(
            changes,
            lambda: SlugHistory.objects.bulk_create(entries),
            ctx=ctx,
            action=OLD_SLUG_RECORDED,
            dry_run=dry_run,
        )
    return changes


def old_slug_redirect(language: str, slug: str) -> str | None:
    """The current product path an old slug moves to (one hop); None if the slug is not old,
    or its product has no current slug in that language."""
    product_id = (
        SlugHistory.objects.filter(language=language, slug=slug)
        .values_list("product_id", flat=True)
        .first()
    )
    if product_id is None:
        return None
    current = (
        ProductTranslation.objects.filter(product_id=product_id, language=language)
        .exclude(slug="")
        .values_list("slug", flat=True)
        .first()
    )
    if current is None:
        return None
    return slugs.product_path(language, current, default_language=settings.LANGUAGE_CODE)
