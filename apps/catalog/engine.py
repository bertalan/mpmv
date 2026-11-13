"""Pure catalog rules (spec 03 §3.2): the vendor SKU match key and warnings (CAT.13), price
validation, the rules of the category and term trees and of the attributes a taxonomy declares for
its terms, the shape of a product's typed attributes, variant axes and variants (CAT.3), and the
redirect rules: safe same-site paths, matching, loops and chains (CAT.6). No ORM here."""

import datetime as dt
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from html.parser import HTMLParser
from typing import Any, Protocol, cast
from urllib.parse import unquote_to_bytes, urlsplit
from zoneinfo import ZoneInfo

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


# A price column is MoneyField = DecimalField(12, 2): at most 10 digits before the point.
MAX_PRICE = Decimal(10) ** 10
# A stock column is IntegerField (PostgreSQL integer).
MAX_STOCK = 2**31 - 1


def to_price(value: object) -> Decimal:
    """An exact, non-negative amount with 2 decimals.

    Floats are refused (TypeError). More than 2 decimals are refused, not rounded: a price is
    entered, not computed, so a third decimal is an input error.
    """
    amount = to_decimal(value)
    if not amount.is_finite():
        raise ValueError(f"{value!r} is not a finite amount")
    if abs(amount) >= MAX_PRICE:  # before quantize, which fails past the context precision
        raise ValueError(f"{value!r} is too large for a price")
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
    if sale_price_net is not None and sale_price_net == 0:
        errors["sale_price_net"] = ZERO_SALE_PRICE
    elif sale_price_net is None:
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
# A sale or an override at 0.00 would sell for nothing. Owner to confirm for the sale price
# (a 100% sale would need this rule relaxed).
ZERO_SALE_PRICE = "A sale price must be above 0.00."
ZERO_OVERRIDE = "A price override must be above 0.00; leave it empty to use the product's price."


def override_error(price_net_override: Decimal | None) -> str | None:
    """A variant override is empty (the product's price) or above 0.00."""
    return ZERO_OVERRIDE if price_net_override is not None and price_net_override == 0 else None


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


class Overriding(Protocol):
    @property
    def price_net_override(self) -> Decimal | None: ...


def public_variant_price(product: Priced, variant: Overriding) -> Decimal | None:
    """The net price buyers may see for `variant`: its override, else the product's price.

    None ("Price on request") unless the product is for sale: an override of an `on_request` or
    `catalog_only` product is never public, like the product's own price.
    """
    if public_price(product) is None:
        return None
    return product.price_net if variant.price_net_override is None else variant.price_net_override


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
# The columns' precision: ProductAttribute.numeric_value is DecimalField(12, 3), the product
# weight and a variant's weight override DecimalField(8, 3).
NUMBER_DIGITS, NUMBER_DECIMALS = 12, 3
WEIGHT_DIGITS, WEIGHT_DECIMALS = 8, 3


def decimal_fit_error(value: Decimal, max_digits: int, decimal_places: int) -> str | None:
    """Why a finite `value` does not fit a DecimalField(max_digits, decimal_places), or None.

    Too many decimals are refused, not rounded, so what is validated is what is stored.
    """
    integer_digits = max_digits - decimal_places
    if abs(value) >= Decimal(10) ** integer_digits:
        return f"has more than {integer_digits} digits before the decimal point"
    if value != value.quantize(Decimal(1).scaleb(-decimal_places)):
        return f"has more than {decimal_places} decimals"
    return None


def weight_error(value: object) -> str | None:
    """Why `value` is not a valid weight in kg (None: no weight), or None; ends with a period."""
    if value is None:
        return None
    if not isinstance(value, Decimal) or not value.is_finite():
        return "needs an exact decimal number."
    if value < 0:
        return "cannot be negative."
    error = decimal_fit_error(value, WEIGHT_DIGITS, WEIGHT_DECIMALS)
    return None if error is None else f"{error}."


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
        error = decimal_fit_error(given.number, NUMBER_DIGITS, NUMBER_DECIMALS)
        return [] if error is None else [f"{code} {error}."]
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
    skus: dict[str, str] = {}  # match key (CAT.13) -> the SKU as first given
    for variant in variants:
        errors.extend(_variant_errors(variant, axes, allowed))
        key = sku_match_key(variant.sku)
        if key in skus:
            errors.append(f"The variant SKU {skus[key]} is given twice.")
        else:
            skus[key] = variant.sku
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


# --- Redirects (CAT.6, spec 03 §3.1-3.2)

REDIRECT_PATH_MAX_LENGTH = 500
_OFF_SITE = 'Enter a path on this site that starts with a single "/", e.g. /old-page/.'


def redirect_path_error(path: str, *, allow_query: bool) -> str | None:
    """Why `path` cannot be a redirect source (`allow_query=False`) or target; None when it can.

    Only same-site paths: an absolute URL, a scheme-relative `//host`, a backslash (which
    browsers read as a slash), whitespace or control characters (header injection with CR/LF)
    and dot segments are refused, so a redirect can never send a visitor off the site.
    """
    if not path:
        return "Enter a path."
    if len(path) > REDIRECT_PATH_MAX_LENGTH:
        return f"A path cannot be longer than {REDIRECT_PATH_MAX_LENGTH} characters."
    if any(char.isspace() or unicodedata.category(char)[0] == "C" for char in path):
        return "A path cannot contain spaces or control characters."
    if "\\" in path:
        return "A path cannot contain backslashes."
    if not path.startswith("/") or path.startswith("//"):
        return _OFF_SITE
    parts = urlsplit(path)
    if parts.scheme or parts.netloc:
        return _OFF_SITE
    if not allow_query and ("?" in path or "#" in path):
        return "A source is a path only, without a query (?) or a fragment (#)."
    if not allow_query and "%" in path:
        # Sources are stored decoded (`normalise_redirect_source`), as request paths arrive.
        return 'A source cannot hold "%" once decoded: it is double-encoded or not UTF-8.'
    if any(segment in (".", "..") for segment in parts.path.split("/")):
        return 'A path cannot contain "." or ".." segments.'
    return None


# Paths a prefix rule never covers: there a 404 is an API, asset or sign-in answer, not a page.
RESERVED_PREFIXES = (
    "/admin/",
    "/django-admin/",
    "/api/",
    "/media/",
    "/static/",
    "/manager/",
    "/account/",
    "/login/",
    "/logout/",
    "/webhooks/",
    "/i18n/",
    "/geocoding/",
    "/vendor/claim/",
    "/vendor/apply/",
    "/_styleguide/",
)

# The reserved roots that are public pages: they exist in every language (`/it/vendor/apply/`),
# so a configured language prefix is stripped before matching these, and only these. The others
# (files, API, admins) have no language prefix: `/it/media/` is an ordinary path.
I18N_RESERVED_PREFIXES = ("/vendor/apply/",)


def normalise_redirect_source(path: str) -> str:
    """A source as Django hands over a request path: percent-decoded once, as UTF-8.

    Legacy maps often hold encoded URLs (`/caf%C3%A9/`), but `request.path_info` is decoded
    (`/café/`), so an encoded source would never match. Bytes that are not UTF-8 stay encoded,
    and `redirect_path_error` refuses a source still holding "%", so decoding is idempotent and
    the checks then run on what is stored.
    """
    try:
        return unquote_to_bytes(path).decode("utf-8")
    except UnicodeDecodeError:
        return path


def prefix_source_error(path: str, *, languages: Iterable[str]) -> str | None:
    """Why `path` cannot be the source of a prefix rule: it must end with "/", never cover a
    whole site (the root or a language root), or every 404 would redirect, and never cover a
    reserved path (`RESERVED_PREFIXES`). None when it can."""
    if not path.endswith("/"):
        return 'A prefix ends with "/", e.g. /author/.'
    if path == "/" or path.strip("/") in set(languages):
        return "A prefix cannot cover the whole site or a whole language."
    first, _, rest = path.lstrip("/").partition("/")
    unprefixed = f"/{rest}" if first in set(languages) and rest else path
    reserved = next((root for root in RESERVED_PREFIXES if path.startswith(root)), None) or next(
        (root for root in I18N_RESERVED_PREFIXES if unprefixed.startswith(root)), None
    )
    if reserved is not None:
        return f"A prefix cannot cover {reserved}: it is reserved (admin, API, files, sign-in)."
    return None


def path_language(path: str, *, languages: Iterable[str], default: str) -> str:
    """The language of a site path: its `/xx/` prefix when configured, else the default."""
    first = path.lstrip("/").split("/", 1)[0]
    return first if first in set(languages) else default


def _slash_variants(path: str) -> tuple[str, ...]:
    """A path as requested, and with the trailing slash added when it has none."""
    return (path,) if path.endswith("/") else (path, f"{path}/")


def rule_matches(source: str, *, prefix: bool, path: str) -> bool:
    """Whether a rule on `source` (exact, or everything below it) applies to `path`."""
    if prefix:
        return any(candidate.startswith(source) for candidate in _slash_variants(path))
    return source in _slash_variants(path)


def redirect_lookup_candidates(path: str) -> tuple[list[str], list[str]]:
    """The sources an exact rule and a prefix rule would need to match `path`, so the lookup
    is a single indexed query. Prefixes come longest first."""
    exact = list(_slash_variants(path))
    full = exact[-1]
    prefixes = [full[: index + 1] for index, char in enumerate(full) if char == "/"]
    return exact, sorted(set(prefixes), key=len, reverse=True)


def with_query(target: str, query: str) -> str:
    """The redirect location: the request's query string is kept, unless the target has its
    own (a search)."""
    if not query or "?" in target:
        return target
    return f"{target}?{query}"


@dataclass(frozen=True)
class RedirectRule:
    """An active redirect for the loop and chain check: its source, whether it is a prefix,
    and its target path (None for a 410 or a target outside the check)."""

    source: str
    prefix: bool
    target: str | None


class _RuleIndex:
    def __init__(self, rules: Iterable[RedirectRule]) -> None:
        self.exact = {rule.source for rule in rules if not rule.prefix}
        self.prefixes = {rule.source for rule in rules if rule.prefix}

    def match(self, path: str) -> list[str]:
        exact, prefixes = redirect_lookup_candidates(path)
        return [s for s in exact if s in self.exact] + [s for s in prefixes if s in self.prefixes]


def redirect_problems(rules: Sequence[RedirectRule], *, checked: Iterable[str]) -> dict[str, str]:
    """The loops and chains that involve the `checked` sources, by source.

    A rule loops when its target falls under its own source; it chains when its target falls
    under another rule's source (a 410 included), or when another rule's target falls under it.
    Every legacy URL must reach its page in a single hop (spec 13, MIG.6).
    """
    wanted = set(checked)
    index = _RuleIndex(rules)
    problems: dict[str, str] = {}
    for rule in rules:
        if rule.target is None:
            continue
        target = urlsplit(rule.target).path
        for source in index.match(target):
            if source == rule.source:
                if rule.source in wanted:
                    problems.setdefault(rule.source, f"{rule.source} redirects to itself (a loop).")
                continue
            if rule.source in wanted:
                problems.setdefault(
                    rule.source,
                    f"{rule.source} leads to {rule.target}, which the rule on {source} redirects "
                    "again (a chain): point it at the final page.",
                )
            if source in wanted:
                problems.setdefault(
                    source,
                    f"The rule on {rule.source} already leads to {rule.target}: a rule on "
                    f"{source} would make a chain; point that rule at the final page instead.",
                )
    return problems


# --- Product search (CAT.9, spec 03 §3.1 "Search by SKU" and §3.2): query normalisation, the
# text search configuration of each language, and the ranking of the hits. The queries are in
# `services.search`.

SEARCH_MIN_LENGTH = 2
SEARCH_MAX_LENGTH = 100  # characters kept from a query, cut on a word boundary
# Raw characters read before any normalisation: NFKC can expand a code point up to 18 times, so
# a huge input is cut first. Leading spaces and control characters count against it.
SEARCH_RAW_MAX_LENGTH = SEARCH_MAX_LENGTH * 4
SEARCH_MAX_WORDS = 8
SKU_PREFIX_MIN_LENGTH = 4  # a shorter query matches a whole SKU only (legacy rule)
SEARCH_RESULT_LIMIT = 200
# A trigram hit (a typo) weighs less than a full-text hit of the same strength.
SEARCH_TRIGRAM_WEIGHT = 0.5

# PostgreSQL text search configuration (stemming, stop words) of each site language. A language
# added to settings.LANGUAGES needs a row here and its index (catalog_ptrans_fts_<code>).
SEARCH_LANGUAGE_CONFIGS: Mapping[str, str] = {
    "en": "english",
    "it": "italian",
    "de": "german",
    "fr": "french",
    "es": "spanish",
}
SEARCH_FALLBACK_CONFIG = "simple"

SEARCH_TOO_SHORT = "Type at least two characters to search."


def search_config(language: str) -> str:
    """The PostgreSQL text search configuration of `language` ("simple" when unknown)."""
    return SEARCH_LANGUAGE_CONFIGS.get(language, SEARCH_FALLBACK_CONFIG)


@dataclass(frozen=True)
class SearchTerms:
    """A visitor's query ready for the database.

    `text` feeds full-text and trigram search; `sku_key` is the SKU match key of the whole query
    (None when no SKU can be that long); `sku_prefix` allows a prefix match on it. A query with
    an `error` searches nothing. The text is plain words: `-`, quotes and OR are no operators.
    """

    text: str = ""
    sku_key: str | None = None
    sku_prefix: bool = False
    error: str | None = None

    @property
    def has_words(self) -> bool:
        """Whether the text has a letter or digit; punctuation only ("--", quotes) gives full
        text and trigram search nothing to match, so they are skipped."""
        return any(char.isalnum() for char in self.text)


def _search_char(char: str) -> str:
    category = unicodedata.category(char)
    if category == "Cc":  # tabs, newlines, NUL: a word break
        return " "
    if category.startswith("C"):  # format and unassigned characters (zero-width, soft hyphen)
        return ""
    return char


def normalise_search_query(raw: str) -> SearchTerms:
    """NFKC, control characters dropped, whitespace collapsed, at most SEARCH_MAX_WORDS words
    and SEARCH_MAX_LENGTH characters (a longer query is cut on a word boundary, not refused).
    The raw input is cut to SEARCH_RAW_MAX_LENGTH characters before any of this."""
    raw = raw[:SEARCH_RAW_MAX_LENGTH]
    text = "".join(_search_char(char) for char in unicodedata.normalize("NFKC", raw))
    text = " ".join(text.split()[:SEARCH_MAX_WORDS])
    if len(text) > SEARCH_MAX_LENGTH:
        head = text[: SEARCH_MAX_LENGTH + 1]
        text = head.rsplit(" ", 1)[0] if " " in head else text[:SEARCH_MAX_LENGTH]
    if len(text) < SEARCH_MIN_LENGTH:
        return SearchTerms(error=SEARCH_TOO_SHORT)
    key = sku_match_key(text)
    if len(key) > SKU_MAX_LENGTH:
        return SearchTerms(text=text)
    return SearchTerms(text=text, sku_key=key, sku_prefix=len(key) >= SKU_PREFIX_MIN_LENGTH)


@dataclass(frozen=True)
class SearchHit:
    """What one query found about a product: a SKU of it (or of one of its variants) equal to or
    starting with the query, its full-text rank and its trigram word similarity (0 to 1)."""

    product_id: int
    sku_exact: bool = False
    sku_prefix: bool = False
    text_rank: float = 0.0
    similarity: float = 0.0


def _merged(a: SearchHit, b: SearchHit) -> SearchHit:
    return SearchHit(
        product_id=a.product_id,
        sku_exact=a.sku_exact or b.sku_exact,
        sku_prefix=a.sku_prefix or b.sku_prefix,
        text_rank=max(a.text_rank, b.text_rank),
        similarity=max(a.similarity, b.similarity),
    )


def search_score(hit: SearchHit) -> float:
    return hit.text_rank + SEARCH_TRIGRAM_WEIGHT * hit.similarity


def rank_search_hits(hits: Iterable[SearchHit], *, limit: int = SEARCH_RESULT_LIMIT) -> list[int]:
    """Product ids, best first: an exact SKU, then a SKU prefix, then by text score; the newest
    product (highest id) first on a tie. The hits of one product are merged first."""
    best: dict[int, SearchHit] = {}
    for hit in hits:
        known = best.get(hit.product_id)
        best[hit.product_id] = hit if known is None else _merged(known, hit)

    def key(hit: SearchHit) -> tuple[bool, bool, float, int]:
        return (not hit.sku_exact, not hit.sku_prefix, -search_score(hit), -hit.product_id)

    return [hit.product_id for hit in sorted(best.values(), key=key)[:limit]]


# --- Price history and the Omnibus reference price (CMP.6, directive 98/6/EC art. 6a)
#
# A reduction may be announced (strikethrough price) only against the lowest price applied in
# the 30 days before the reduction started. Prices are the net ones the vendor sets; the
# comparison is the same net or gross. Days are business-time-zone calendar days (D-31).

OMNIBUS_DAYS = 30


@dataclass(frozen=True)
class PricePoint:
    """One recorded price setting: the regular price and the optional scheduled sale."""

    recorded_at: dt.datetime
    price_net: Decimal | None
    sale_price_net: Decimal | None
    sale_from: dt.datetime | None
    sale_to: dt.datetime | None
    currency: str = "EUR"


# Owner decision Q1 (2026-11-11): a variant's own price override wins over the product's sale, so
# a sale never reaches a variant that has an override. The Omnibus reference follows the same rule.
SALE_APPLIES_TO_OVERRIDDEN_VARIANT = False


@dataclass(frozen=True)
class ReferencePrice:
    """The prior price to show next to a reduced price, with when the reduction started."""

    price_net: Decimal
    current_net: Decimal
    reduction_started_at: dt.datetime


@dataclass(frozen=True)
class _Segment:
    start: dt.datetime
    end: dt.datetime | None  # None: open-ended
    price: Decimal | None
    is_sale: bool


def _segments(points: Iterable[PricePoint]) -> list[_Segment]:
    """The price in force over time. A setting holds until the next one is recorded; inside it a
    sale holds from `sale_from` (or the recording) to `sale_to`. Zero-length spans are dropped,
    so a setting replaced at the same instant never applied."""
    ordered = sorted(points, key=lambda p: p.recorded_at)  # stable: ties keep their order
    segments: list[_Segment] = []
    for index, point in enumerate(ordered):
        start = point.recorded_at
        end = ordered[index + 1].recorded_at if index + 1 < len(ordered) else None
        cuts = [start]
        if point.sale_price_net is not None:
            for edge in (point.sale_from, point.sale_to):
                if edge is not None and edge > start and (end is None or edge < end):
                    cuts.append(edge)
        cuts = sorted(set(cuts))
        for cut_index, cut in enumerate(cuts):
            cut_end = cuts[cut_index + 1] if cut_index + 1 < len(cuts) else end
            if cut_end is not None and cut_end <= cut:
                continue
            on_sale = (
                point.sale_price_net is not None
                and point.price_net is not None
                and (point.sale_from is None or point.sale_from <= cut)
                and (point.sale_to is None or cut < point.sale_to)
            )
            price = point.sale_price_net if on_sale else point.price_net
            segments.append(_Segment(cut, cut_end, price, on_sale))
    return segments


def _window_start(reduction_start: dt.datetime, tz: ZoneInfo, days: int) -> dt.datetime:
    local_day = reduction_start.astimezone(tz).date() - dt.timedelta(days=days)
    return dt.datetime.combine(local_day, dt.time(), tzinfo=tz)


def reference_price(
    points: Iterable[PricePoint],
    *,
    at: dt.datetime,
    tz: ZoneInfo,
    days: int = OMNIBUS_DAYS,
    currency: str = "EUR",
) -> ReferencePrice | None:
    """The lowest price of the `days` calendar days before the reduction in force at `at`.

    None when no reduction is in force, when there is no history before it, or when that lowest
    price is not above the reduced one (nothing to announce). Successive reductions keep the
    price before the first one: the reduction starts when the continuous run of reduced
    prices does. The reduced price itself is never part of the window.
    """
    segments = _segments(p for p in points if p.currency == currency)
    current = next((s for s in segments if s.start <= at and (s.end is None or at < s.end)), None)
    if current is None or not current.is_sale or current.price is None:
        return None
    first = segments.index(current)
    while (
        first > 0
        and segments[first - 1].is_sale
        and segments[first - 1].end == (segments[first].start)
    ):
        first -= 1
    reduction_start = segments[first].start
    window_start = _window_start(reduction_start, tz, days)
    prior = [
        s.price
        for s in segments[:first]
        if s.price is not None
        and (s.end is None or s.end > window_start)
        and s.start < reduction_start
    ]
    if not prior:
        return None
    lowest = min(prior)
    if lowest <= current.price:
        return None
    return ReferencePrice(
        price_net=lowest, current_net=current.price, reduction_started_at=reduction_start
    )


# --- Brands and manufacturers (CAT.20): the pure rules behind duplicate detection and links.

WIKIDATA_ID_RE = re.compile(r"Q[1-9][0-9]{0,9}")


def brand_name_key(name: str) -> str:
    """The key two spellings of one brand share (`Acme & Sons`, `ACME and sons`): NFKD without
    accents, case-folded, `&` read as `and`, punctuation dropped, whitespace collapsed. A key of
    `A.C.M.E.` and `ACME` is the same on purpose. Empty for a name with no letters or digits."""
    text = unicodedata.normalize("NFKC", name).replace("&", " and ")
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    # Dots and apostrophes vanish (A.C.M.E. = ACME); other punctuation separates words.
    text = re.sub("[.'\u2019]", "", text)
    text = re.sub("[^0-9a-z\u0080-\uffff]+", " ", text)
    return " ".join(text.split())


def wikidata_id_error(value: str) -> str | None:
    """None for an empty value or a Wikidata item ID like `Q42`; the format only, nothing is
    fetched."""
    if value == "" or WIKIDATA_ID_RE.fullmatch(value):
        return None
    return "A Wikidata item ID is a Q followed by a number, e.g. Q42."


def https_url_error(value: str) -> str | None:
    """None for an absolute `https://` URL with a host, no credentials and no spaces."""
    text = value.strip()
    if text != value or any(ch.isspace() for ch in value):
        return "A link cannot contain spaces."
    try:
        parts = urlsplit(value)
        host = parts.hostname
    except ValueError:
        return "This is not a valid link."
    if parts.scheme != "https" or not host:
        return "Links must be absolute https:// addresses."
    if parts.username is not None or parts.password is not None:
        return "A link cannot contain a user name or password."
    return None


def same_as_errors(urls: Sequence[str]) -> list[str]:
    """One message per refused entry of a `sameAs` list: not an https link, or listed twice."""
    errors: list[str] = []
    seen: set[str] = set()
    for url in urls:
        problem = https_url_error(url) if isinstance(url, str) else "Links must be text."
        if problem:
            errors.append(f"{url!r}: {problem}")
        elif url in seen:
            errors.append(f"{url!r}: listed twice.")
        seen.add(url)
    return errors


def link_field_errors(url: str, same_as: object) -> dict[str, list[str]]:
    """The errors of an official `url` and a `sameAs` list, by field name."""
    errors: dict[str, list[str]] = {}
    if url and (problem := https_url_error(url)):
        errors["url"] = [problem]
    if not isinstance(same_as, list):
        errors["same_as"] = ["Enter a list of links."]
    elif found := same_as_errors(same_as):
        errors["same_as"] = found
    return errors


# --- Brand structured data (CAT.20 3/3, spec 10 SEO.7): graph nodes with stable `@id`s

SCHEMA_ORG = "https://schema.org"
WIKIDATA_ENTITY = "https://www.wikidata.org/wiki/"
BRAND_FRAGMENT = "#brand"
_JSONLD_ESCAPES = {code: f"\\u{code:04x}" for code in (0x3C, 0x3E, 0x26, 0x2028, 0x2029)}


def jsonld_text(data: Mapping[str, Any]) -> str:
    """`data` as JSON safe inside `<script type="application/ld+json">`: markup characters
    are escaped, so the text cannot close the element or open another."""
    return json.dumps(data, ensure_ascii=False).translate(_JSONLD_ESCAPES)


class _PlainText(HTMLParser):
    """The text of HTML with entities decoded; script and style content is dropped."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip += 1
        else:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self._skip = max(0, self._skip - 1)
        self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def plain_description(html: str) -> str:
    """A rich-text description as one line of plain text, for structured data."""
    parser = _PlainText()
    parser.feed(html)
    parser.close()
    return " ".join("".join(parser.parts).split())


def https_links(urls: Iterable[object]) -> list[str]:
    """The valid absolute `https://` links of `urls`, each once, in order (a stored value that
    skipped validation never reaches a page)."""
    found: list[str] = []
    for url in urls:
        if isinstance(url, str) and https_url_error(url) is None and url not in found:
            found.append(url)
    return found


def wikidata_url(wikidata_id: str) -> str:
    """The Wikidata page of an item ID; empty for no ID or one of the wrong format."""
    if wikidata_id and wikidata_id_error(wikidata_id) is None:
        return f"{WIKIDATA_ENTITY}{wikidata_id}"
    return ""


def brand_node_id(paths: Mapping[str, str], *, source: str, site: str) -> str:
    """The `@id` of a brand: its listing page URL plus `#brand`. The page of the source language
    when there is one, else the first language by code, so the id is the same whatever language
    or vendor page asks. Empty when the brand has no public page."""
    path = paths.get(source) or (paths[min(paths)] if paths else "")
    return f"{site.rstrip('/')}{path}{BRAND_FRAGMENT}" if path else ""


def brand_node(
    *,
    node_id: str,
    name: str,
    website: str = "",
    logo_url: str = "",
    description: str = "",
    wikidata_id: str = "",
    same_as: Iterable[object] = (),
    manufacturer_id: str = "",
) -> dict[str, Any]:
    """The schema.org `Brand` node. `sameAs` lists the Wikidata item first, then the https
    links; an empty value is left out."""
    links = https_links([wikidata_url(wikidata_id), *same_as])
    site = website if https_url_error(website) is None else ""
    optional: dict[str, Any] = {
        "url": site,
        "logo": logo_url,
        "description": description,
        "sameAs": links,
        "manufacturer": {"@id": manufacturer_id} if manufacturer_id else "",
    }
    node: dict[str, Any] = {"@type": "Brand", "@id": node_id, "name": name}
    node.update({key: value for key, value in optional.items() if value})
    return node


def manufacturer_node(
    *,
    node_id: str,
    legal_name: str,
    url: str = "",
    same_as: Iterable[object] = (),
    country: str = "",
    brand_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """The schema.org `Organization` node of a brand's manufacturer, pointing at its brands."""
    site = url if https_url_error(url) is None else ""
    optional: dict[str, Any] = {
        "url": site,
        "sameAs": https_links(same_as),
        "address": {"@type": "PostalAddress", "addressCountry": country} if country else "",
        "brand": [{"@id": brand_id} for brand_id in brand_ids],
    }
    node: dict[str, Any] = {
        "@type": "Organization",
        "@id": node_id,
        "name": legal_name,
        "legalName": legal_name,
    }
    node.update({key: value for key, value in optional.items() if value})
    return node


def brand_graph(nodes: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """The `@graph` document of a brand page."""
    return {"@context": SCHEMA_ORG, "@graph": [dict(node) for node in nodes]}


# --- Product structured data (CAT.7, spec 10 SEO.7)

PRODUCT_FRAGMENT = "#product"


def product_node(
    *,
    url: str,
    name: str,
    sku: str,
    description: str = "",
    brand_id: str = "",
    mpn: str = "",
    gtin: str = "",
) -> dict[str, Any]:
    """The schema.org `Product` node of a product page. The brand is a reference to the brand
    page's node (CAT.20), never a copy. No `offers`: the page shows an interim net price (TAX.12
    replaces it with the gross one) and an on-request product has none; an empty value is left
    out."""
    optional: dict[str, Any] = {
        "description": description,
        "brand": {"@id": brand_id} if brand_id else "",
        "mpn": mpn,
        "gtin": gtin,
    }
    node: dict[str, Any] = {
        "@type": "Product",
        "@id": f"{url}{PRODUCT_FRAGMENT}",
        "url": url,
        "name": name,
        "sku": sku,
    }
    node.update({key: value for key, value in optional.items() if value})
    return node
