"""Pure catalog rules (spec 03 §3.2): SKU normalization, price validation, and the rules of the
category and term trees and of the attributes a taxonomy declares for its terms. No ORM here."""

import datetime as dt
import re
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any, cast

from apps.core.money import CENT, to_decimal

SKU_MAX_LENGTH = 64


class InvalidSku(ValueError):
    """The SKU is empty once normalized."""


def canonical_sku(value: str) -> str:
    """The stored form of a SKU: no surrounding whitespace, upper case. Never raises."""
    return value.strip().upper()


def normalize_sku(value: str) -> str:
    """`canonical_sku`, refusing a SKU that is empty once normalized."""
    sku = canonical_sku(value)
    if not sku:
        raise InvalidSku("A SKU cannot be empty.")
    return sku


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
    price_net: Decimal,
    sale_price_net: Decimal | None,
    sale_from: dt.datetime | None,
    sale_to: dt.datetime | None,
) -> dict[str, str]:
    """Consistency of a regular price with its optional sale, by field name; empty when valid.

    Same rules as the database check constraints on Product.
    """
    errors: dict[str, str] = {}
    if sale_price_net is None:
        if sale_from is not None or sale_to is not None:
            errors["sale_price_net"] = "A sale period needs a sale price."
    elif sale_price_net >= price_net:
        errors["sale_price_net"] = "The sale price must be lower than the price."
    if sale_from is not None and sale_to is not None and sale_to <= sale_from:
        errors["sale_to"] = "The sale must end after it starts."
    return errors


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
