"""Pure catalog rules (spec 03 §3.2): the vendor SKU match key and warnings (CAT.13), price
validation, the rules of the category and term trees and of the attributes a taxonomy declares for
its terms, and the shape of a product's typed attributes, variant axes and variants (CAT.3). No ORM
here."""

import datetime as dt
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol, cast

from apps.core.money import CENT, to_decimal

SKU_MAX_LENGTH = 64

# Characters that commonly break exports, URLs, spreadsheets or matching (spec 03 §3.2).
_SKU_SLASHES = frozenset("/\\")
_SKU_QUOTES = frozenset("'\"`\u2018\u2019\u201a\u201b\u201c\u201d\u201e\u00b4")


def sku_match_key(raw: str) -> str:
    """The key a vendor SKU is matched on: NFKC, trimmed, inner whitespace collapsed to one
    space, case-folded. Never raises; an empty key means an unusable SKU (`sku_errors`)."""
    text = unicodedata.normalize("NFKC", raw)
    text = " ".join(text.split()).casefold()
    return unicodedata.normalize("NFKC", text)


def sku_errors(raw: str) -> list[str]:
    """Why a SKU cannot be used at all; empty when usable. Odd characters are warnings."""
    key = sku_match_key(raw)
    if not key:
        return ["A SKU cannot be empty."]
    if len(key) > SKU_MAX_LENGTH:
        return [f"A SKU cannot be longer than {SKU_MAX_LENGTH} characters once normalised."]
    return []


def sku_warnings(raw: str) -> list[str]:
    """Characters that commonly cause trouble in a SKU. Never blocks: the SKU is kept as
    entered, the vendor is told."""
    warnings: list[str] = []
    if raw != raw.strip():
        warnings.append("starts or ends with a space")
    inner = raw.strip()
    if "  " in inner or any(c.isspace() and c != " " for c in inner):
        warnings.append("contains repeated spaces, tabs or line breaks")
    if any(c in _SKU_SLASHES for c in raw):
        warnings.append("contains a slash")
    if any(c in _SKU_QUOTES for c in raw):
        warnings.append("contains quotes")
    if any(unicodedata.category(c)[0] == "C" and not c.isspace() for c in raw):
        warnings.append("contains control or invisible characters")
    if any(
        ord(c) > 127 and not unicodedata.category(c).startswith("C") and c not in _SKU_QUOTES
        for c in raw
    ):
        warnings.append("contains non-ASCII characters that can look like others")
    return warnings


def to_price(value: object) -> Decimal:
    """An exact, non-negative amount with 2 decimals.

    Floats are refused (TypeError). More than 2 decimals are refused, not rounded: a price is
    entered, not computed, so a third decimal is an input error.
    """
    amount = to_decimal(value)
    if not amount.is_finite():
        raise ValueError(f"{value!r} is not a finite amount")
    if amount != amount.quantize(CENT):
        raise ValueError(f"{value!r} has more than 2 decimals")
    if amount < 0:
        raise ValueError(f"{value!r} is negative")
    return amount.quantize(CENT)


def price_errors(
    price_net: Decimal | None,
    sale_price_net: Decimal | None,
    sale_from: dt.datetime | None,
    sale_to: dt.datetime | None,
) -> dict[str, str]:
    """Consistency of a regular price with its optional sale, by field name; empty when valid.

    Same rules as the database check constraints on Product. No price (None) is consistent
    here; whether the sales mode needs one is `price_required_error`'s rule.
    """
    errors: dict[str, str] = {}
    if price_net is not None and price_net == 0:
        errors["price_net"] = ZERO_PRICE
    if sale_price_net is None:
        if sale_from is not None or sale_to is not None:
            errors["sale_price_net"] = "A sale period needs a sale price."
    elif price_net is None:
        errors["sale_price_net"] = "A sale needs a regular price."
    elif sale_price_net >= price_net:
        errors["sale_price_net"] = "The sale price must be lower than the price."
    if sale_from is not None and sale_to is not None and sale_to <= sale_from:
        errors["sale_to"] = "The sale must end after it starts."
    return errors


# --- Price visibility (CAT.14, owner decision Q-31)

BUY = "buy"  # the only sales mode with a public price (models.SalesMode.BUY)
ZERO_PRICE = "A price cannot be 0.00; leave it empty when there is no price."
PRICE_REQUIRED = "A product for sale needs a price."


class Priced(Protocol):
    @property
    def sales_mode(self) -> str: ...

    @property
    def price_net(self) -> Decimal | None: ...


def price_required_error(sales_mode: str, price_net: Decimal | None) -> str | None:
    """Only a product for sale (`buy`) needs a price; the others may keep none."""
    return PRICE_REQUIRED if sales_mode == BUY and price_net is None else None


def public_price(product: Priced) -> Decimal | None:
    """The net price buyers may see: None ("Price on request") unless the product is for sale.

    An `on_request` or `catalog_only` product keeps its last stored price for the vendor and
    staff, but it is never public: not on pages, feeds or JSON-LD. Back to `buy`, it shows again.
    """
    return product.price_net if product.sales_mode == BUY else None


# --- Trees (CAT.2): categories and terms use treebeard materialized paths.


def ancestor_paths(path: str, steplen: int) -> list[str]:
    """The paths of the ancestors of the node at `path`, root first.

    A materialized path is the parent's path plus `steplen` characters, so the ancestors are its
    prefixes. A product tagged with a lower-level term implies the upper levels (spec 03).
    """
    if not path or len(path) % steplen:
        raise ValueError(f"{path!r} is not a tree path of {steplen} characters per level")
    return [path[:end] for end in range(steplen, len(path), steplen)]


def term_placement_error(
    *, hierarchical: bool, taxonomy: object, parent_taxonomy: object | None
) -> str | None:
    """Why a term of `taxonomy` cannot go under a parent of `parent_taxonomy` (None: top level)."""
    if parent_taxonomy is None:
        return None
    if parent_taxonomy != taxonomy:
        return "A term can only be placed under a term of the same taxonomy."
    if not hierarchical:
        return "This taxonomy is flat: its terms cannot have children."
    return None


# --- Term attributes: each taxonomy declares the extra fields of its terms, as data.

TERM_ATTRIBUTE_KINDS = ("text", "number", "boolean")
_ATTRIBUTE_KEYS = {"code", "kind", "required", "label"}
_ATTRIBUTE_CODE = re.compile(r"^[a-z][a-z0-9_]*$")


def term_schema_errors(schema: object) -> list[str]:
    """Problems of a taxonomy's `term_attributes` declaration; empty when valid.

    The declaration is a list of `{"code", "kind", "required"?, "label"?}` objects: `code` is the
    key in each term's `attributes`, `kind` one of TERM_ATTRIBUTE_KINDS, `label` a staff hint.
    """
    if not isinstance(schema, list):
        return ["The term attributes must be a list of attribute definitions."]
    errors: list[str] = []
    seen: set[str] = set()
    for position, entry in enumerate(schema, start=1):
        if not isinstance(entry, dict):
            errors.append(f"Attribute {position} must be an object with a code and a kind.")
            continue
        code = entry.get("code")
        if not isinstance(code, str) or not _ATTRIBUTE_CODE.match(code):
            errors.append(f"Attribute {position} needs a code: lower case letters, digits and _.")
            continue
        if code in seen:
            errors.append(f"An attribute code is declared twice: {code}.")
        seen.add(code)
        if entry.get("kind") not in TERM_ATTRIBUTE_KINDS:
            kinds = ", ".join(TERM_ATTRIBUTE_KINDS)
            errors.append(f"Attribute {code}: the kind must be one of {kinds}.")
        if not isinstance(entry.get("required", False), bool):
            errors.append(f"Attribute {code}: required must be true or false.")
        if not isinstance(entry.get("label", ""), str):
            errors.append(f"Attribute {code}: the label must be a text.")
        unknown = sorted(entry.keys() - _ATTRIBUTE_KEYS)
        errors.extend(f"Attribute {code}: unknown key {key}." for key in unknown)
    return errors


def _kind_error(code: str, kind: str, value: object) -> str | None:
    if kind == "number":
        # JSON numbers; a boolean is not a number even though Python's bool is an int.
        if isinstance(value, bool) or not isinstance(value, int | float):
            return f"{code} must be a number."
    elif kind == "boolean":
        if not isinstance(value, bool):
            return f"{code} must be true or false."
    elif not isinstance(value, str):
        return f"{code} must be a text."
    return None


INVALID_SCHEMA = "The taxonomy declares its term attributes wrongly: correct the taxonomy first."


def term_attribute_errors(schema: object, values: object) -> list[str]:
    """Problems of a term's `attributes` against its taxonomy's declaration.

    A declaration stored without validation (an import, a raw update) is checked first, so a
    malformed one is reported, not met with a KeyError.
    """
    if term_schema_errors(schema):
        return [INVALID_SCHEMA]
    if not isinstance(values, dict):
        return ["The attributes must be an object of attribute codes and values."]
    entries = cast(Sequence[Mapping[str, Any]], schema)  # checked above
    declared = {str(entry["code"]): entry for entry in entries}
    errors: list[str] = []
    for code, entry in declared.items():
        if code not in values:
            if entry.get("required", False):
                errors.append(f"{code} is required.")
            continue
        error = _kind_error(code, str(entry["kind"]), values[code])
        if error:
            errors.append(error)
    errors.extend(
        f"{code} is not an attribute of this taxonomy." for code in values if code not in declared
    )
    return errors


# --- Typed attributes and variant axes (CAT.3, spec 03 §3.2, owner decision Q-32)
#
# A product lists the attributes that describe it. A choice attribute takes values from the
# attribute's own list (translated labels), a number attribute one exact number (in the
# attribute's unit), a text attribute one language-neutral text (an author, an ISBN). The choice
# attributes a variable product varies on are its variant axes, in the order the product lists
# them; every variant takes exactly one of the product's values on each axis.

ATTRIBUTE_KINDS = ("choice", "number", "text")
VARIANT_LABEL_SEPARATOR = " / "


@dataclass(frozen=True)
class AttributeSpec:
    """What the platform knows of an attribute: its kind and, for a choice, its value codes."""

    kind: str
    values: frozenset[str] = frozenset()


@dataclass(frozen=True)
class AttributeIn:
    """One attribute of a product as a client sends it."""

    code: str
    values: tuple[str, ...] = ()
    number: Decimal | None = None
    text: str = ""
    axis: bool = False


@dataclass(frozen=True)
class VariantIn:
    """One variant: its SKU, its value code on each axis, and its own overrides and stock."""

    sku: str
    options: Mapping[str, str]
    price_net_override: Decimal | None = None
    weight_kg_override: Decimal | None = None
    stock_qty: int = 0
    is_default: bool = False


@dataclass(frozen=True)
class TranslationIn:
    language: str
    title: str
    short_description: str = ""
    description: str = ""


@dataclass(frozen=True)
class ProductIn:
    """A new product with its texts, attributes and (variable products only) variants."""

    sku: str
    price_net: Decimal
    kind: str = "simple"
    sales_mode: str = "buy"
    fiscal_category: str = "standard"
    weight_kg: Decimal | None = None
    translations: tuple[TranslationIn, ...] = ()
    attributes: tuple[AttributeIn, ...] = ()
    variants: tuple[VariantIn, ...] = field(default=())


def _attribute_errors(spec: AttributeSpec, given: AttributeIn) -> list[str]:
    code = given.code
    has_number, has_text = given.number is not None, bool(given.text)
    if spec.kind == "choice":
        if has_number or has_text:
            return [f"{code} takes values, not a number or a text."]
        if not given.values:
            return [f"{code} needs at least one value."]
        errors: list[str] = []
        seen: set[str] = set()
        for value in given.values:
            if value in seen:
                errors.append(f"{code} lists the value {value} twice.")
            elif value not in spec.values:
                errors.append(f"{code} has no value {value}.")
            seen.add(value)
        return errors
    if given.axis:
        return [f"{code} cannot be a variant axis: only choice attributes can."]
    if spec.kind == "number":
        if given.values or has_text:
            return [f"{code} takes a number only."]
        if given.number is None:
            return [f"{code} needs a number."]
        if not isinstance(given.number, Decimal) or not given.number.is_finite():
            return [f"{code} needs an exact decimal number."]
        return []
    if given.values or has_number:
        return [f"{code} takes a text only."]
    return [] if has_text else [f"{code} needs a text."]


def _variant_errors(
    variant: VariantIn, axes: Sequence[str], allowed: Mapping[str, Iterable[str]]
) -> list[str]:
    errors = [
        f"Variant {variant.sku} has no {axis}." for axis in axes if axis not in variant.options
    ]
    for code, value in variant.options.items():
        if code not in allowed:
            errors.append(f"Variant {variant.sku}: {code} is not a variant axis of this product.")
        elif value not in allowed[code]:
            errors.append(
                f"Variant {variant.sku}: {value} is not one of the product's {code} values."
            )
    return errors


def _is_choice(catalogue: Mapping[str, AttributeSpec], code: str) -> bool:
    spec = catalogue.get(code)
    return spec is not None and spec.kind == "choice"


def product_structure_errors(
    kind: str,
    catalogue: Mapping[str, AttributeSpec],
    attributes: Sequence[AttributeIn],
    variants: Sequence[VariantIn],
) -> list[str]:
    """Why a product's attributes, axes and variants do not fit together; empty when valid.

    `catalogue` maps the platform's attribute codes to their spec. Owner decision Q-32: only a
    variable product has variant axes and variants, and it needs at least one of each.
    """
    errors: list[str] = []
    seen: set[str] = set()
    for given in attributes:
        if given.code in seen:
            errors.append(f"{given.code} is given twice.")
            continue
        seen.add(given.code)
        spec = catalogue.get(given.code)
        if spec is None:
            errors.append(f"{given.code} is not a known attribute.")
            continue
        errors.extend(_attribute_errors(spec, given))

    axes = [a.code for a in attributes if a.axis and _is_choice(catalogue, a.code)]
    allowed = {a.code: a.values for a in attributes if a.axis}
    if kind != "variable":
        if axes:
            errors.append("Only a variable product has variant axes.")
        if variants:
            errors.append("Only a variable product has variants.")
        return errors
    if not axes:
        errors.append("A variable product needs at least one variant axis.")
    if not variants:
        errors.append("A variable product needs at least one variant.")

    combinations: dict[tuple[str, ...], str] = {}
    skus: set[str] = set()
    for variant in variants:
        errors.extend(_variant_errors(variant, axes, allowed))
        sku = variant.sku
        if sku in skus:
            errors.append(f"The variant SKU {sku} is given twice.")
        skus.add(sku)
        combination = tuple(variant.options.get(axis, "") for axis in axes)
        if combination in combinations:
            errors.append(
                f"Variants {combinations[combination]} and {variant.sku} have the same options."
            )
        else:
            combinations[combination] = variant.sku
    if sum(variant.is_default for variant in variants) > 1:
        errors.append("Only one variant can be the default.")
    return errors


def variant_order(
    axes: Sequence[str],
    positions: Mapping[str, Mapping[str, int]],
    options: Sequence[Mapping[str, str]],
) -> list[int]:
    """Indexes of `options` in display order: by the first axis, then the next, each by the
    position of the value in its attribute's list (S, M, L rather than alphabetical)."""

    def key(index: int) -> tuple[int, ...]:
        return tuple(positions[axis][options[index][axis]] for axis in axes)

    return sorted(range(len(options)), key=key)


def variant_label(labels: Sequence[str]) -> str:
    """A variant's name from its option labels in axis order, e.g. "M / Red"."""
    return VARIANT_LABEL_SEPARATOR.join(labels)
