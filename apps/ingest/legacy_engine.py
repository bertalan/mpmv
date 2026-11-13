"""Pure rules of the legacy extractor (MIG.1, spec 13 §13.2 "Rules the extractor and loaders
follow"). No ORM, no I/O, no Django: `legacy_source` reads the rows and calls these functions."""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Final

from apps.catalog.engine import sku_match_key

# --- Money

_MONEY_RE: Final = re.compile(r"-?(?:\d+(?:\.\d+)?|\.\d+)")


class LegacyDataError(ValueError):
    """A legacy value that cannot be trusted. Reported, never defaulted."""


def parse_money(raw: object) -> Decimal:
    """Parse a legacy money string with `Decimal`. Rejects empty, padded, comma-decimal,
    exponent, NaN/Infinity and non-string values; the value is returned exactly (no rounding,
    the loaders round per line)."""
    if not isinstance(raw, str) or not _MONEY_RE.fullmatch(raw):
        raise LegacyDataError(f"Malformed money value: {raw!r}")
    try:
        return Decimal(raw)
    except InvalidOperation as exc:  # pragma: no cover - the regex already guards this
        raise LegacyDataError(f"Malformed money value: {raw!r}") from exc


# --- VAT-like fee line guard

_VAT_LIKE_RE: Final = re.compile(r"\b(?:vat|iva|tax|taxes|imposta|mwst|tva|btw)\b", re.IGNORECASE)


class VatLikeFeeError(LegacyDataError):
    """A fee line that looks like VAT: live orders carry VAT only as native tax lines."""


def guard_vat_like_fee(item_type: str, name: str) -> None:
    """Fail on a `fee` order item whose name looks like VAT."""
    if item_type == "fee" and _VAT_LIKE_RE.search(name):
        raise VatLikeFeeError(f"VAT-like fee line: {name!r}")


# --- Customers

CUSTOMER_ROLE: Final = "customer"
ORDER_WINDOW_MONTHS: Final = 24
DEFAULT_ACTIVITY_WINDOW_MONTHS: Final = 12
_SERIALIZED_ROLE_RE: Final = re.compile(r's:\d+:"([^"]+)";b:1')


def roles_from_meta(raw: str) -> frozenset[str]:
    """Roles in a capabilities meta value: a PHP-serialized map or a plain role name."""
    found = _SERIALIZED_ROLE_RE.findall(raw)
    return frozenset(found) if found else frozenset({raw.strip()} - {""})


def months_before(moment: datetime, months: int) -> datetime:
    """`moment` minus whole calendar months, the day clamped to the target month."""
    index = moment.year * 12 + (moment.month - 1) - months
    year, month = divmod(index, 12)
    month += 1
    for day in (moment.day, 30, 29, 28):
        try:
            return moment.replace(year=year, month=month, day=day)
        except ValueError:
            continue
    raise AssertionError("unreachable")  # pragma: no cover


def is_active_customer(
    roles: Iterable[str],
    *,
    last_order_at: datetime | None,
    last_active_at: datetime | None,
    now: datetime,
    activity_window_months: int = DEFAULT_ACTIVITY_WINDOW_MONTHS,
) -> bool:
    """Customer role, plus an order in the last 24 months or activity (the last-active meta)
    within the vendor's MIG.16 window (default 12 months). Any other role is spam. The meta
    is set on every account, so the role filter always comes first."""
    if CUSTOMER_ROLE not in set(roles):
        return False
    if last_order_at is not None and last_order_at >= months_before(now, ORDER_WINDOW_MONTHS):
        return True
    return last_active_at is not None and last_active_at >= months_before(
        now, activity_window_months
    )


# --- Products: vendor + SKU match key + translation group, never SKU alone


@dataclass(frozen=True, order=True)
class ProductKey:
    vendor_id: int
    sku_key: str
    group_id: int


def product_key(vendor_id: int, sku: str, group_id: int) -> ProductKey:
    key = sku_match_key(sku)
    if not key:
        raise LegacyDataError("A product without a usable SKU has no match key.")
    return ProductKey(vendor_id, key, group_id)


@dataclass(frozen=True)
class KeyedPost:
    post_id: int
    key: ProductKey
    language: str


def find_key_collisions(posts: Sequence[KeyedPost]) -> list[tuple[ProductKey, str, list[int]]]:
    """Posts sharing a key and a language: a translation group holds one post per language."""
    seen: dict[tuple[ProductKey, str], list[int]] = {}
    for post in posts:
        seen.setdefault((post.key, post.language), []).append(post.post_id)
    return [(key, lang, ids) for (key, lang), ids in sorted(seen.items()) if len(ids) > 1]


# --- Redirect sources


class RedirectSourceKind(StrEnum):
    CUSTOM_OLD_SLUGS = "custom_old_slugs"
    CORE_OLD_SLUG = "core_old_slug"
    RETIRED_SKU_MAP = "retired_sku_map"
    MERGED_TAG_MAP_EN = "merged_tag_map_en"
    MERGED_TAG_MAP_IT = "merged_tag_map_it"
    OLD_CATEGORY_MAP = "old_category_map"
    OLD_BRAND_BASE = "old_brand_base"
    TRASHED_PRODUCT = "trashed_product"


# The seven sources listed in the spec's Redirects row; trashed products come on top of them.
REDIRECT_SOURCE_KINDS: Final = tuple(
    kind for kind in RedirectSourceKind if kind is not RedirectSourceKind.TRASHED_PRODUCT
)
CUSTOM_OLD_SLUGS_CAP: Final = 10
_TRASH_SUFFIX_RE: Final = re.compile(r"__trashed(?:-\d+)?$")


def strip_trash_suffix(slug: str) -> str:
    return _TRASH_SUFFIX_RE.sub("", slug)


def split_old_slugs(raw: str) -> list[str]:
    """Slugs of the custom meta: newline or comma separated, trimmed, de-duplicated in order."""
    out: list[str] = []
    for part in re.split(r"[\n,]", raw):
        slug = part.strip().strip("/")
        if slug and slug not in out:
            out.append(slug)
    return out


# --- Tax archive: the only order data the extractor may read

TAX_ARCHIVE_ORDER_META: Final = (
    "_billing_first_name",
    "_billing_last_name",
    "_billing_company",
    "_billing_address_1",
    "_billing_address_2",
    "_billing_city",
    "_billing_state",
    "_billing_postcode",
    "_billing_country",
    "_billing_vat_number",
    "_order_currency",
    "_order_total",
    "_order_tax",
    "_order_shipping",
    "_order_shipping_tax",
    "_cart_discount",
    "_customer_user",
    "_paid_date",
)
FORBIDDEN_META_PATTERNS: Final = (
    "shipping_first_name",
    "shipping_last_name",
    "shipping_company",
    "shipping_address",
    "shipping_city",
    "shipping_state",
    "shipping_postcode",
    "shipping_country",
    "phone",
    "email",
    "ip_address",
    "user_agent",
    "geolocation",
    "attribution",
    "token",
    "password",
    "session",
    "payment_method_token",
    "customer_id",
    "bank",
    "note",
)


def assert_tax_archive_keys(keys: Iterable[str]) -> None:
    """Defence in depth: the read list is a whitelist and must also hold no forbidden pattern."""
    for key in keys:
        lowered = key.lower()
        if key not in TAX_ARCHIVE_ORDER_META or any(p in lowered for p in FORBIDDEN_META_PATTERNS):
            raise LegacyDataError(f"Order meta key outside the tax-archive read list: {key!r}")


# --- Order store drift check


@dataclass(frozen=True)
class OrderSnapshot:
    order_id: int
    status: str
    total: Decimal


@dataclass(frozen=True)
class DriftFinding:
    order_id: int
    kind: str  # missing_in_posts | missing_in_secondary | status | total
    detail: str


def compare_order_stores(
    posts: Mapping[int, OrderSnapshot], secondary: Mapping[int, OrderSnapshot]
) -> list[DriftFinding]:
    """Disagreements between the authoritative posts store and the other store. Never raises:
    the extractor reads the posts store and reports each finding."""
    findings: list[DriftFinding] = []
    for order_id in sorted(posts.keys() | secondary.keys()):
        a, b = posts.get(order_id), secondary.get(order_id)
        if b is None:
            findings.append(DriftFinding(order_id, "missing_in_secondary", "only in posts store"))
        elif a is None:
            findings.append(DriftFinding(order_id, "missing_in_posts", "only in secondary store"))
        else:
            if a.status != b.status:
                findings.append(DriftFinding(order_id, "status", f"{a.status} != {b.status}"))
            if a.total != b.total:
                findings.append(DriftFinding(order_id, "total", f"{a.total} != {b.total}"))
    return findings
