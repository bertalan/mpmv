"""Catalog services: audited price changes (CAT.1), audited product and variant edits (CAT.12),
taxonomy queries (CAT.2), the catalogue integrity rules (CAT.14), and products created with
typed attributes, variant axes and variants (CAT.3), product slugs with their history (CAT.5),
and the listing query (CAT.8).

Every price change goes through here, whatever the adapter (admin, API, import, MCP): it is
validated, returned as a ChangeSet (dry-run) and written to the audit log. The 30-day price
history for the Omnibus rule (CMP.6) can be built from these audit rows.
"""

import datetime as dt
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import NON_FIELD_ERRORS, PermissionDenied, ValidationError
from django.db import models, transaction
from django.db.models import Prefetch, Q, QuerySet
from django.utils import timezone

from apps.core import translations
from apps.core.audit import AuditContext, record
from apps.core.changeset import ChangeSet
from apps.core.services import audit_changes, run_mutation
from apps.vendors.models import Vendor
from apps.vendors.scoping import VendorScopedQuerySet
from apps.vendors.services import vendors_in_suggestions

from . import engine, listings, slugs
from .models import (
    Attribute,
    AttributeValue,
    Brand,
    Category,
    CategoryTranslation,
    FiscalCategory,
    Product,
    ProductAttribute,
    ProductKind,
    ProductStatus,
    ProductTranslation,
    SalesMode,
    SlugHistory,
    StockStatus,
    Taxonomy,
    TaxonomyTranslation,
    Term,
    TermTranslation,
    Variant,
    VariantOption,
)

logger = logging.getLogger(__name__)

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


# --- Audited product and variant edits (CAT.12)
#
# Every create, change and delete of a product or variant, whatever the adapter, goes through
# these services. The row is saved whole, but the audit entry exposes the facts that matter
# for tax, shipping and selling: `fiscal_category`, `sales_mode`, weight and dimensions (plus
# the SKU and the owning vendor, so that entries are readable and filterable by vendor).
# Prices are refused here: they change only through the price services above. The owning
# vendor never changes (owner decision Q-33, CAT.14).

PRODUCT_CREATED = "catalog.product.created"
PRODUCT_UPDATED = "catalog.product.updated"
PRODUCT_DELETED = "catalog.product.deleted"
PRODUCT_RELATIONS_CHANGED = "catalog.product.relations_changed"
VARIANT_CREATED = "catalog.variant.created"
VARIANT_UPDATED = "catalog.variant.updated"
VARIANT_DELETED = "catalog.variant.deleted"

PRODUCT_EDIT_FIELDS = (
    "sales_mode",
    "fiscal_category",
    "weight_kg",
    "length_cm",
    "width_cm",
    "height_cm",
)
VARIANT_EDIT_FIELDS = ("weight_kg_override",)
PRODUCT_RELATIONS = ("secondary_categories", "terms", "upsells")

VENDOR_NEVER_CHANGES = (
    "The owning vendor of a product never changes; staff duplicate the product to the new "
    "vendor instead."
)


def product_snapshot(product: Product) -> dict[str, Any]:
    return {
        "sku": product.sku,
        "vendor": str(product.vendor.public_id),
        **{name: getattr(product, name) for name in PRODUCT_EDIT_FIELDS},
    }


def variant_snapshot(variant: Variant) -> dict[str, Any]:
    return {
        "sku": variant.sku,
        "product": str(variant.product.public_id),
        "vendor": str(variant.product.vendor.public_id),
        **{name: getattr(variant, name) for name in VARIANT_EDIT_FIELDS},
    }


def _refuse_price_changes(stored: Any, edited: Any, fields: tuple[str, ...]) -> None:
    changed = [name for name in fields if getattr(stored, name) != getattr(edited, name)]
    if changed:
        message = "Prices change only through the audited price services."
        raise ValidationError(dict.fromkeys(changed, message))


def _validate(instance: Product | Variant) -> None:
    """Field, choice and constraint checks, also on dry-run (the database would refuse it)."""
    instance.full_clean(validate_unique=False)


def save_product(product: Product, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Create or update `product`, auditing its sales mode, fiscal category, weight and size.

    The change is computed against the stored row (locked), never a possibly stale instance.
    Other fields are saved but not audited. Raises ValidationError when the owning vendor or a
    price would change, or a value is invalid, also on dry-run.
    """
    _validate(product)
    changes = ChangeSet()
    key = str(product.public_id)
    with transaction.atomic():
        if product._state.adding:
            changes.create(PRODUCT_MODEL, key, product_snapshot(product))
            action = PRODUCT_CREATED
        else:
            stored = (
                Product.objects.select_for_update(of=("self",))
                .select_related("vendor")
                .get(pk=product.pk)
            )
            if stored.vendor_id != product.vendor_id:
                raise ValidationError({"vendor": VENDOR_NEVER_CHANGES})
            _refuse_price_changes(stored, product, PRODUCT_PRICE_FIELDS)
            changes.update(PRODUCT_MODEL, key, product_snapshot(stored), product_snapshot(product))
            action = PRODUCT_UPDATED
        if dry_run:
            return changes
        product.save()
        audit_changes(changes, ctx=ctx, action=action)
    return changes


def delete_product(product: Product, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Delete `product` with its variants and translations, auditing the product and each
    variant. A product that others require as their companion is protected (ProtectedError)."""
    product_changes, variant_changes = ChangeSet(), ChangeSet()
    with transaction.atomic():
        stored = (
            Product.objects.select_for_update(of=("self",))
            .select_related("vendor")
            .get(pk=product.pk)
        )
        product_changes.delete(PRODUCT_MODEL, str(stored.public_id), product_snapshot(stored))
        for variant in stored.variants.select_for_update().select_related("product__vendor"):
            variant_changes.delete(VARIANT_MODEL, str(variant.public_id), variant_snapshot(variant))
        if not dry_run:
            stored.delete()
            audit_changes(product_changes, ctx=ctx, action=PRODUCT_DELETED)
            audit_changes(variant_changes, ctx=ctx, action=VARIANT_DELETED)
    return ChangeSet([*product_changes.changes, *variant_changes.changes])


def relations_snapshot(product: Product) -> dict[str, Any]:
    """The product's many-to-many links (classification and suggestions), as public ids."""
    return {
        "vendor": str(product.vendor.public_id),
        **{
            name: sorted(
                str(public_id)
                for public_id in getattr(product, name).values_list("public_id", flat=True)
            )
            for name in PRODUCT_RELATIONS
        },
    }


def record_relations_change(
    product: Product, before: dict[str, Any], *, ctx: AuditContext
) -> ChangeSet:
    """Audit the many-to-many links a form has just saved in this transaction (the admin
    saves them after the row), when they differ from `before` (`relations_snapshot`)."""
    changes = ChangeSet()
    changes.update(PRODUCT_MODEL, str(product.public_id), before, relations_snapshot(product))
    audit_changes(changes, ctx=ctx, action=PRODUCT_RELATIONS_CHANGED)
    return changes


def save_variant(variant: Variant, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Create or update `variant`, auditing its weight override; audited like products.

    A variant belongs to its product's vendor: moving it to a product of another vendor is
    refused, as are price changes (`set_variant_price`).
    """
    _validate(variant)
    changes = ChangeSet()
    key = str(variant.public_id)
    with transaction.atomic():
        if variant._state.adding:
            changes.create(VARIANT_MODEL, key, variant_snapshot(variant))
            action = VARIANT_CREATED
        else:
            stored = (
                Variant.objects.select_for_update(of=("self",))
                .select_related("product__vendor")
                .get(pk=variant.pk)
            )
            if stored.vendor_id != variant.product.vendor_id:
                raise ValidationError({"product": VENDOR_NEVER_CHANGES})
            _refuse_price_changes(stored, variant, VARIANT_PRICE_FIELDS)
            changes.update(VARIANT_MODEL, key, variant_snapshot(stored), variant_snapshot(variant))
            action = VARIANT_UPDATED
        if dry_run:
            return changes
        variant.save()
        audit_changes(changes, ctx=ctx, action=action)
    return changes


def delete_variant(variant: Variant, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Delete `variant` and its translations, audited."""
    changes = ChangeSet()
    with transaction.atomic():
        stored = (
            Variant.objects.select_for_update(of=("self",))
            .select_related("product__vendor")
            .get(pk=variant.pk)
        )
        changes.delete(VARIANT_MODEL, str(stored.public_id), variant_snapshot(stored))
        if dry_run:
            return changes
        stored.delete()
        audit_changes(changes, ctx=ctx, action=VARIANT_DELETED)
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


# --- Products with typed attributes, variant axes and variants (CAT.3, spec 03 §3.2, Q-32)

type Catalogue = dict[str, tuple[Attribute, dict[str, AttributeValue]]]


def _add_error(errors: dict[str, list[str]], field: str, message: str) -> None:
    errors.setdefault(field, []).append(message)


def _check_choice(errors: dict[str, list[str]], field: str, value: str, allowed: list[str]) -> None:
    if value not in allowed:
        _add_error(errors, field, f"{value!r} is not one of {', '.join(allowed)}.")


def _amount(errors: dict[str, list[str]], field: str, value: object) -> Any:
    if value is None:
        return None
    try:
        return engine.to_price(value)
    except (TypeError, ValueError) as exc:
        _add_error(errors, field, str(exc))
        return None


def _attribute_catalogue(codes: set[str]) -> Catalogue:
    rows = Attribute.objects.filter(code__in=codes).prefetch_related("values")
    return {a.code: (a, {v.value: v for v in a.values.all()}) for a in rows}


def _product_errors(
    vendor: Vendor, data: engine.ProductIn, catalogue: Catalogue
) -> dict[str, list[str]]:
    """Every reason the payload cannot be created, by field (NON_FIELD_ERRORS: its shape)."""
    errors: dict[str, list[str]] = {}
    price = _amount(errors, "price_net", data.price_net)
    if price is not None and price == 0:  # CAT.14, Q-31: never 0.00
        _add_error(errors, "price_net", engine.ZERO_PRICE)
    _check_choice(errors, "kind", data.kind, list(ProductKind.values))
    _check_choice(errors, "sales_mode", data.sales_mode, list(SalesMode.values))
    _check_choice(errors, "fiscal_category", data.fiscal_category, list(FiscalCategory.values))
    languages = [code for code, _ in settings.LANGUAGES]
    seen: set[str] = set()
    for text in data.translations:
        _check_choice(errors, "translations", text.language, languages)
        if text.language in seen:
            _add_error(errors, "translations", f"{text.language} is given twice.")
        seen.add(text.language)

    specs = {
        code: engine.AttributeSpec(attribute.kind, frozenset(values))
        for code, (attribute, values) in catalogue.items()
    }
    for message in engine.product_structure_errors(
        data.kind, specs, data.attributes, data.variants
    ):
        _add_error(errors, NON_FIELD_ERRORS, message)
    for variant in data.variants:
        _amount(errors, "variants", variant.price_net_override)
        if variant.stock_qty < 0:
            _add_error(errors, "variants", f"{variant.sku}: the stock cannot be negative.")

    if Product.objects.filter(vendor=vendor, sku=engine.canonical_sku(data.sku)).exists():
        _add_error(errors, "sku", "This vendor already has a product with this SKU.")
    skus = [engine.canonical_sku(v.sku) for v in data.variants]
    taken = Variant.objects.filter(vendor=vendor, sku__in=skus).values_list("sku", flat=True)
    for sku in sorted(taken):
        _add_error(errors, "variants", f"This vendor already has a variant with this SKU: {sku}.")
    return errors


def create_product(
    vendor: Vendor,
    data: engine.ProductIn,
    *,
    ctx: AuditContext,
    dry_run: bool = False,
) -> ChangeSet:
    """Create `vendor`'s product with its texts, typed attributes, variant axes and variants.

    The whole payload is validated first (`engine.product_structure_errors`; owner decision
    Q-32: variants only on variable products), then returned as a ChangeSet of creations and,
    unless `dry_run`, written and audited in one transaction. The product's attributes keep the
    order they are given in, so the first axis given is the first axis shown; the variants are
    numbered in display order: by the first axis, then the next, each by the position of the
    value in its attribute (S, M, L). Raises ValidationError, also on dry-run.
    """
    catalogue = _attribute_catalogue({a.code for a in data.attributes})
    errors = _product_errors(vendor, data, catalogue)
    if errors:
        raise ValidationError(errors)

    vendor_key = str(vendor.public_id)
    product = Product(
        vendor=vendor,
        sku=engine.canonical_sku(data.sku),
        kind=data.kind,
        sales_mode=data.sales_mode,
        fiscal_category=data.fiscal_category,
        price_net=engine.to_price(data.price_net),
        weight_kg=data.weight_kg,
    )
    product_key = str(product.public_id)
    changes = ChangeSet()
    changes.create(
        PRODUCT_MODEL,
        product_key,
        {
            "vendor": vendor_key,
            "sku": product.sku,
            "kind": product.kind,
            "sales_mode": product.sales_mode,
            "fiscal_category": product.fiscal_category,
            "price_net": product.price_net,
            "weight_kg": product.weight_kg,
        },
    )

    texts = [
        ProductTranslation(
            product=product,
            language=text.language,
            title=text.title,
            short_description=text.short_description,
            description=text.description,
        )
        for text in data.translations
    ]
    for text in texts:
        changes.create(
            "catalog.producttranslation",
            str(text.public_id),
            {
                "vendor": vendor_key,
                "product": product_key,
                "language": text.language,
                "title": text.title,
            },
        )

    rows: list[tuple[ProductAttribute, list[AttributeValue]]] = []
    for position, given in enumerate(data.attributes):
        attribute, values = catalogue[given.code]
        row = ProductAttribute(
            product=product,
            attribute=attribute,
            numeric_value=given.number,
            text_value=given.text,
            is_variant_axis=given.axis,
            position=position,
        )
        rows.append((row, [values[value] for value in given.values]))
        changes.create(
            "catalog.productattribute",
            str(row.public_id),
            {
                "vendor": vendor_key,
                "product": product_key,
                "attribute": given.code,
                "values": list(given.values),
                "numeric_value": given.number,
                "text_value": given.text,
                "is_variant_axis": given.axis,
                "position": position,
            },
        )

    axes = [given.code for given in data.attributes if given.axis]
    positions = {code: {v: row.position for v, row in catalogue[code][1].items()} for code in axes}
    order = engine.variant_order(axes, positions, [v.options for v in data.variants])
    variants: list[tuple[Variant, list[AttributeValue]]] = []
    for position, index in enumerate(order):
        given_variant = data.variants[index]
        variant = Variant(
            product=product,
            vendor=vendor,
            sku=engine.canonical_sku(given_variant.sku),
            price_net_override=_amount({}, "variants", given_variant.price_net_override),
            weight_kg_override=given_variant.weight_kg_override,
            stock_qty=given_variant.stock_qty,
            is_default=given_variant.is_default,
            position=position,
        )
        options = [catalogue[code][1][given_variant.options[code]] for code in axes]
        variants.append((variant, options))
        variant_key = str(variant.public_id)
        changes.create(
            VARIANT_MODEL,
            variant_key,
            {
                "vendor": vendor_key,
                "product": product_key,
                "sku": variant.sku,
                "position": position,
                "is_default": variant.is_default,
                "price_net_override": variant.price_net_override,
                "weight_kg_override": variant.weight_kg_override,
                "stock_qty": variant.stock_qty,
            },
        )
        for code, value in zip(axes, options, strict=True):
            changes.create(
                "catalog.variantoption",
                f"{variant_key}:{code}",
                {
                    "vendor": vendor_key,
                    "variant": variant_key,
                    "attribute": code,
                    "value": value.value,
                },
            )

    def apply() -> None:
        product.save()
        for text in texts:
            text.save()
        for row, values in rows:
            row.save()
            if values:
                row.values.set(values)
        for variant, options in variants:
            variant.save()
            VariantOption.objects.bulk_create(
                VariantOption(variant=variant, attribute_id=value.attribute_id, value=value)
                for value in options
            )

    return run_mutation(changes, apply, ctx=ctx, action=PRODUCT_CREATED, dry_run=dry_run)


def _text(rows: Any, field: str, language: str, default: str) -> str:
    """The published text in `language`, else in EN, else `default`; reads prefetched rows."""
    resolved = translations.resolve(rows, language)
    return default if resolved.translation is None else str(getattr(resolved.translation, field))


def attribute_name(attribute: Attribute, language: str) -> str:
    """The attribute's name in `language` (EN fallback); its code when it has no name yet."""
    return _text(attribute.translations.all(), "name", language, attribute.code)


def value_label(value: AttributeValue, language: str) -> str:
    """The value's label in `language` (EN fallback); its code when it has no label yet."""
    return _text(value.translations.all(), "label", language, value.value)


def attribute_catalogue() -> "QuerySet[Attribute]":
    """Every attribute with its names, values and labels prefetched, in display order."""
    return Attribute.objects.prefetch_related(
        "translations",
        Prefetch("values", queryset=AttributeValue.objects.prefetch_related("translations")),
    )


def product_attributes(product: Product) -> "QuerySet[ProductAttribute]":
    """The product's attributes in display order, with names, values and labels prefetched."""
    return (
        ProductAttribute.objects.filter(product_id=product.pk)
        .select_related("attribute")
        .prefetch_related(
            "attribute__translations",
            Prefetch("values", queryset=AttributeValue.objects.prefetch_related("translations")),
        )
        .order_by("position", "id")
    )


def variant_axes(product: Product) -> list[ProductAttribute]:
    """The attributes the product's variants vary on, first axis first."""
    return list(product_attributes(product).filter(is_variant_axis=True))


def variant_options(variant: Variant) -> list[VariantOption]:
    """The variant's options in the axis order of its product."""
    axis_order = dict(
        ProductAttribute.objects.filter(product_id=variant.product_id).values_list(
            "attribute_id", "position"
        )
    )
    options = VariantOption.objects.filter(variant_id=variant.pk).select_related(
        "attribute", "value"
    )
    return sorted(
        options.prefetch_related("value__translations"),
        key=lambda option: axis_order.get(option.attribute_id, 0),
    )


def variant_label(variant: Variant, language: str) -> str:
    """The variant's name in `language` from its translated option labels, e.g. "M / Rosso"."""
    labels = [value_label(option.value, language) for option in variant_options(variant)]
    return engine.variant_label(labels)


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


# --- Listings (CAT.8): the query behind category, term, brand and vendor pages and the CMS
# product grid (CMS.10). Parsing and URLs are pure (`listings`); slugs and querysets are here.

LISTING_ORDER = ("-created_at", "-pk")  # newest first; the pk keeps pages stable
LISTING_PER_PAGE = 24


@dataclass(frozen=True)
class ListingScope:
    """The fixed context of a listing: the page it is on, or the CMS grid's selection.

    Each given part narrows the listing; a category or term includes its subtree.
    """

    category: Category | None = None
    term: Term | None = None
    brand: Brand | None = None
    vendor: Vendor | None = None


@dataclass(frozen=True)
class ListingFilter:
    """Visitor filters resolved to tree paths and objects.

    Categories are OR-ed; term groups are AND-ed, the terms of one group (one taxonomy) OR-ed,
    each with its subtree. A price bound keeps only products with a public price (Q-31).
    """

    category_paths: tuple[str, ...] = ()
    term_groups: tuple[tuple[str, ...], ...] = ()
    vendor: Vendor | None = None
    price_min: Decimal | None = None
    price_max: Decimal | None = None
    matches_nothing: bool = False


@dataclass(frozen=True)
class ResolvedFilter:
    filters: ListingFilter
    unknown: tuple[str, ...] = ()  # keys with a value that matched nothing (a stale link)


@dataclass(frozen=True)
class ListingPage:
    params: listings.ListingParams
    products: list[Product]
    window: listings.PageWindow
    unknown: tuple[str, ...]


def listed_products() -> "VendorScopedQuerySet[Product]":
    """Products a visitor may see in a listing: published, of an active vendor.

    Retired products keep their page (spec 03 §3.2) but leave the listings. Products without a
    public price are listed, showing "Price on request".
    """
    return Product.objects.filter(
        status=ProductStatus.PUBLISHED, vendor__status=Vendor.Status.ACTIVE
    )


def _in_subtrees(field: str, paths: Iterable[str]) -> Q:
    q = Q()
    for path in paths:
        q |= Q(**{f"{field}__path__startswith": path})
    return q


def _matching(products: "QuerySet[Product]", condition: Q) -> "QuerySet[Product]":
    """Narrow through a subquery, so a to-many join never duplicates a product."""
    return products.filter(pk__in=Product.objects.filter(condition).values("pk"))


NO_SCOPE = ListingScope()
NO_FILTER = ListingFilter()


def listing_products(
    scope: ListingScope = NO_SCOPE, filters: ListingFilter = NO_FILTER
) -> "QuerySet[Product]":
    """The listed products of `scope` matching `filters`, newest first. Slice it for a grid."""
    scoped = listed_products()
    if filters.matches_nothing:
        return scoped.none()
    for vendor in (scope.vendor, filters.vendor):
        if vendor is not None:
            scoped = scoped.for_vendor(vendor)  # the tenant scope (CORE.3)
    products: QuerySet[Product] = scoped
    if scope.brand is not None:
        products = products.filter(brand=scope.brand)
    category_paths = (scope.category.path,) if scope.category is not None else ()
    for paths in (category_paths, filters.category_paths):
        if paths:
            in_tree = _in_subtrees("category", paths) | _in_subtrees("secondary_categories", paths)
            products = _matching(products, in_tree)
    scope_terms = ((scope.term.path,),) if scope.term is not None else ()
    for group in (*scope_terms, *filters.term_groups):
        products = _matching(products, _in_subtrees("terms", group))
    if filters.price_min is not None or filters.price_max is not None:
        products = products.filter(sales_mode=SalesMode.BUY, price_net__isnull=False)
        if filters.price_min is not None:
            products = products.filter(price_net__gte=filters.price_min)
        if filters.price_max is not None:
            products = products.filter(price_net__lte=filters.price_max)
    return products.order_by(*LISTING_ORDER)


def _paths_by_slug_path(
    rows: "QuerySet[Any]", owner: str, values: Iterable[str], language: str
) -> dict[str, str]:
    """The tree path of the one category or term each slug path names; values that name none,
    or more than one, are left out.

    Each level matches its slug in `language` or, for nodes without a text in it, in the source
    language (explicit EN fallback, D-18). Slugs are unique among siblings in one language only,
    so a level is matched below the previous one; a slug two siblings answer to (one's own
    text, the other's fallback) is ambiguous and matches nothing.
    """
    paths = {value: listings.slug_path(value) for value in values}
    segments = {slug for path in paths.values() for slug in path}
    source = translations.source_language()
    candidates = list(
        rows.filter(slug__in=segments, language__in={language, source}).values_list(
            f"{owner}_id", "language", "slug", f"{owner}__path", f"{owner}__depth"
        )
    )
    translated = set(
        rows.filter(
            language=language, **{f"{owner}_id__in": {c[0] for c in candidates}}
        ).values_list(f"{owner}_id", flat=True)
    )
    level: dict[tuple[int, str], list[str]] = {}
    for owner_id, row_language, slug, tree_path, depth in candidates:
        if row_language == language or owner_id not in translated:
            level.setdefault((depth, slug), []).append(tree_path)
    found: dict[str, str] = {}
    for value, slug_path in paths.items():
        parent = ""
        for depth, slug in enumerate(slug_path, start=1):
            matches = [p for p in level.get((depth, slug), []) if p.startswith(parent)]
            if len(matches) != 1:
                break
            parent = matches[0]
        else:
            found[value] = parent
    return found


def listing_term_keys(language: str) -> dict[str, Taxonomy]:
    """The filterable taxonomies by their slug in `language` (EN fallback): the query keys of
    the term filters. A slug equal to a reserved listing parameter is left out.

    The slug constraint holds per language, so one taxonomy's slug in `language` can equal
    another's EN fallback slug: the slug in `language` wins, and the clash is logged.
    """
    source = translations.source_language()
    by_taxonomy: dict[int, TaxonomyTranslation] = {}
    rows = TaxonomyTranslation.objects.filter(
        taxonomy__filterable=True, language__in={language, source}
    ).select_related("taxonomy")
    for row in rows:
        if row.taxonomy_id not in by_taxonomy or row.language == language:
            by_taxonomy[row.taxonomy_id] = row
    keys: dict[str, TaxonomyTranslation] = {}
    for row in sorted(by_taxonomy.values(), key=lambda r: r.taxonomy_id):
        if not row.slug or row.slug in listings.RESERVED_KEYS:
            continue
        taken = keys.get(row.slug)
        if taken is not None:
            kept, dropped = (taken, row) if taken.language == language else (row, taken)
            logger.warning(
                "Listing filter key %r in %r: taxonomy %s (fallback slug) is hidden by "
                "taxonomy %s; give it a slug in this language.",
                row.slug,
                language,
                dropped.taxonomy.code,
                kept.taxonomy.code,
            )
            row = kept
        keys[row.slug] = row
    return {slug: row.taxonomy for slug, row in keys.items()}


def parse_listing_query(query: Mapping[str, Sequence[str]], language: str) -> listings.ParsedParams:
    """`listings.parse_listing_params` with the taxonomy keys of `language`."""
    return listings.parse_listing_params(query, term_keys=listing_term_keys(language).keys())


def resolve_listing_filter(params: listings.ListingParams, language: str) -> ResolvedFilter:
    """Turn the slugs of `params` into a filter. A value that matches nothing (a renamed term,
    a closed vendor) is reported in `unknown`, and the filter then matches nothing: the
    adapter answers 404 or redirects to the canonical URL without it. Category and term values
    are slug paths from the top of their tree (`parent/child`)."""
    unknown: list[str] = []
    category_paths: tuple[str, ...] = ()
    if params.category:
        category = _paths_by_slug_path(
            CategoryTranslation.objects.all(), "category", [params.category], language
        ).get(params.category)
        if category is not None:
            category_paths = (category,)
        else:
            unknown.append(listings.CATEGORY)
    taxonomies = listing_term_keys(language) if params.terms else {}
    term_groups: list[tuple[str, ...]] = []
    for key, term_slugs in params.terms:
        taxonomy = taxonomies.get(key)
        found: dict[str, str] = {}
        if taxonomy is not None:
            rows = TermTranslation.objects.filter(term__taxonomy=taxonomy)
            found = _paths_by_slug_path(rows, "term", term_slugs, language)
        if any(slug not in found for slug in term_slugs):
            unknown.append(key)
        term_groups.append(tuple(sorted(set(found.values()))))
    vendor = None
    if params.vendor:
        vendor = Vendor.objects.filter(slug=params.vendor, status=Vendor.Status.ACTIVE).first()
        if vendor is None:
            unknown.append(listings.VENDOR)
    filters = ListingFilter(
        category_paths=category_paths,
        term_groups=tuple(term_groups),
        vendor=vendor,
        price_min=params.price_min,
        price_max=params.price_max,
        matches_nothing=bool(unknown),
    )
    return ResolvedFilter(filters=filters, unknown=tuple(sorted(unknown)))


def listing_page(
    scope: ListingScope,
    params: listings.ListingParams,
    language: str,
    *,
    per_page: int = LISTING_PER_PAGE,
) -> ListingPage:
    """One page of a listing: its products, the page window and the unknown filter keys.

    A page past the end has no products and `window.out_of_range` (the adapter answers 404).
    """
    resolved = resolve_listing_filter(params, language)
    products = listing_products(scope, resolved.filters)
    window = listings.page_window(total=products.count(), page=params.page, per_page=per_page)
    rows: list[Product] = []
    if not window.out_of_range:
        selected = products.select_related("vendor", "brand", "category")
        rows = list(selected[window.offset : window.offset + window.limit])
    return ListingPage(params=params, products=rows, window=window, unknown=resolved.unknown)
