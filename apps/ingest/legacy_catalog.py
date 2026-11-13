"""Pure rules of the MIG.3 catalog loader (no ORM): input shape, parsing, grouping.

The loader (`apps.ingest.catalog_loader`, called through `services.load_legacy_catalog`) reads
plain dicts, the shape the MIG.1 extractor yields. MIG.1 is built in parallel and this module
does not import it: the shape below is the contract, and the loader never knows which connector
produced it (spec 13 §13.3).

Input bundle (every key optional; a missing list is an empty one):

    {
      "vendors":    [{"user_id", "display_name", "legal_name", "slug", "country", "email",
                      "phone", "commission_percent",
                      "translations": {"en": {"tagline", "description", "shipping_policy",
                                              "refund_policy", "cancellation_policy"}, "it": {}}}],
      "categories": [{"term_id", "group_id", "lang", "parent_id", "name", "slug",
                      "description"}],
      "brands":     [{"term_id", "group_id", "lang", "name", "slug", "description"}],
      "tags":       [{"term_id", "group_id", "lang", "name", "slug", "role"}],
      "attributes": [{"code", "kind": "choice|number|text", "unit",
                      "labels": {"en": "Colour", "it": "Colore"},
                      "values": [{"group_id", "lang", "value", "label"}]}],
      "posts":      [{"post_id", "post_type": "product|product_variation", "parent_id",
                      "group_id", "lang", "author_id", "status", "sku", "title", "slug",
                      "short_description", "description", "seo_title", "seo_description",
                      "type": "simple|variable", "price_net", "sale_price_net", "sale_from",
                      "sale_to", "stock_qty", "stock_status", "weight_kg", "gtin", "mpn",
                      "category_ids", "tag_ids", "brand_id", "attributes", "options",
                      "companion_sku", "upsell_skus", "commission_override_percent"}],
    }

Options (not part of the bundle): `include_unpublished` is the vendor's MIG.16 choice of drafts
and archived products (default False); `attribute_aliases` maps a legacy attribute code with a
typo or a duplicate name to the one it merges into (the MIG.2 decision list).

Rules (spec 13 §13.2): a product is keyed by vendor + SKU match key + translation group id,
never by the SKU alone; trashed posts are only redirect sources; a draft or archived post is
loaded as a hidden (draft) product only when the vendor chose so.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

PRODUCT = "product"
VARIATION = "product_variation"

PUBLISH = "publish"
TRASH = "trash"
# Statuses that come along as hidden products only when the vendor chose so (MIG.16).
UNPUBLISHED = frozenset({"draft", "pending", "private", "future", "archived"})

LOAD_PUBLISHED = "published"
LOAD_HIDDEN = "hidden"
SKIP_UNPUBLISHED = "skip_unpublished"
SKIP_TRASH = "trash"
SKIP_UNKNOWN = "unknown"

STOCK_STATUSES = {
    "instock": "in_stock",
    "in_stock": "in_stock",
    "outofstock": "out_of_stock",
    "out_of_stock": "out_of_stock",
    "onbackorder": "on_backorder",
    "on_backorder": "on_backorder",
}

CENT = Decimal("0.01")


class BadValue(ValueError):
    """A legacy value that cannot be loaded: the record is rejected and reported."""


def parse_money(raw: object, *, field: str = "price") -> Decimal | None:
    """A legacy money string as a 2-place Decimal (ROUND_HALF_UP); None for empty.

    Malformed, non-finite and negative values are rejected (`BadValue`), never guessed."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if isinstance(raw, float):
        raise BadValue(f"{field}: a float is not a money value")
    try:
        value = Decimal(str(raw).strip())
    except InvalidOperation:
        raise BadValue(f"{field}: {raw!r} is not a number") from None
    if not value.is_finite() or value < 0:
        raise BadValue(f"{field}: {raw!r} is not a valid amount")
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def parse_decimal(raw: object, *, field: str, places: int = 3) -> Decimal | None:
    """A non-negative measure (weight, size) with `places` decimals; None for empty."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    try:
        value = Decimal(str(raw).strip())
    except InvalidOperation:
        raise BadValue(f"{field}: {raw!r} is not a number") from None
    if not value.is_finite() or value < 0:
        raise BadValue(f"{field}: {raw!r} is not a valid measure")
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def parse_quantity(raw: object) -> int:
    """Stock quantity: a whole number, never negative (legacy backorders go below zero)."""
    if raw is None or raw == "":
        return 0
    try:
        return max(0, int(Decimal(str(raw).strip())))
    except (InvalidOperation, ValueError):
        raise BadValue(f"stock quantity: {raw!r} is not a number") from None


def load_decision(status: object, *, include_unpublished: bool) -> str:
    """What to do with a legacy post of `status`: load it live, load it hidden, or skip it."""
    text = str(status or "").strip().lower()
    if text == PUBLISH:
        return LOAD_PUBLISHED
    if text == TRASH:
        return SKIP_TRASH
    if text in UNPUBLISHED:
        return LOAD_HIDDEN if include_unpublished else SKIP_UNPUBLISHED
    return SKIP_UNKNOWN


def stock_status(raw: object) -> str:
    return STOCK_STATUSES.get(str(raw or "instock").strip().lower(), "in_stock")


def group_key(vendor_id: object, sku_key: str, group_id: object) -> tuple[str, str, str]:
    """The identity of a legacy product: vendor + SKU match key + translation group id."""
    return (str(vendor_id), sku_key, str(group_id))


def group_by_language(posts: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """The posts of one translation group by language; the first of a language wins."""
    result: dict[str, Mapping[str, Any]] = {}
    for post in posts:
        result.setdefault(str(post.get("lang", "")).lower(), post)
    return result


def by_group(rows: Iterable[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    """Rows grouped by `group_id`, in input order (a row without a group is its own group)."""
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        key = row.get("group_id")
        groups[str(key) if key not in (None, "") else f"term:{row.get('term_id')}"].append(row)
    return dict(groups)


def missing_languages(present: Iterable[str], languages: Sequence[str], source: str) -> list[str]:
    """The non-source languages of the site that a legacy item has no text in: each one gets
    the fallback notice and a translation-queue entry (owner decision 2026-11-12)."""
    have = set(present)
    return [code for code in languages if code != source and code not in have]
