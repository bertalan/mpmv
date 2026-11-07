"""Catalog services: audited price changes (CAT.1), audited product and variant edits (CAT.12),
taxonomy queries (CAT.2), and products created with typed attributes, variant axes and variants
(CAT.3).

Every price change goes through here, whatever the adapter (admin, API, import, MCP): it is
validated, returned as a ChangeSet (dry-run) and written to the audit log. The 30-day price
history for the Omnibus rule (CMP.6) can be built from these audit rows.
"""

import datetime as dt
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db import transaction
from django.db.models import Prefetch, Q, QuerySet

from apps.core import translations
from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.services import audit_changes, run_mutation
from apps.vendors.models import Vendor

from . import engine
from .models import (
    Attribute,
    AttributeValue,
    Category,
    FiscalCategory,
    Product,
    ProductAttribute,
    ProductKind,
    ProductTranslation,
    SalesMode,
    Term,
    Variant,
    VariantOption,
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


# --- Products with typed attributes, variant axes and variants (CAT.3, spec 03 §3.2, Q-32)

PRODUCT_CREATED = "catalog.product.created"

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


def _stored(value: Decimal | None, decimal_places: int) -> Decimal | None:
    """A validated decimal as its column stores it, so the ChangeSet records the stored value."""
    return None if value is None else value.quantize(Decimal(1).scaleb(-decimal_places))


def _attribute_catalogue(codes: set[str]) -> Catalogue:
    rows = Attribute.objects.filter(code__in=codes).prefetch_related("values")
    return {a.code: (a, {v.value: v for v in a.values.all()}) for a in rows}


def _product_errors(
    vendor: Vendor, data: engine.ProductIn, catalogue: Catalogue
) -> dict[str, list[str]]:
    """Every reason the payload cannot be created, by field (NON_FIELD_ERRORS: its shape)."""
    errors: dict[str, list[str]] = {}
    try:
        engine.normalize_sku(data.sku)
    except engine.InvalidSku as exc:
        _add_error(errors, "sku", str(exc))
    _amount(errors, "price_net", data.price_net)
    weight_error = engine.weight_error(data.weight_kg)
    if weight_error is not None:
        _add_error(errors, "weight_kg", f"The weight {weight_error}")
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
        if not engine.canonical_sku(variant.sku):
            _add_error(errors, "variants", "A variant SKU cannot be empty.")
        _amount(errors, "variants", variant.price_net_override)
        weight_error = engine.weight_error(variant.weight_kg_override)
        if weight_error is not None:
            _add_error(errors, "variants", f"{variant.sku}: the weight override {weight_error}")
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
        weight_kg=_stored(data.weight_kg, engine.WEIGHT_DECIMALS),
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
            numeric_value=_stored(given.number, engine.NUMBER_DECIMALS),
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
                "numeric_value": row.numeric_value,
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
            weight_kg_override=_stored(given_variant.weight_kg_override, engine.WEIGHT_DECIMALS),
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
