"""Pure listing rules (CAT.8, spec 03 §3.2 URLs, owner decision D-07). No ORM here.

A listing page (category, taxonomy term, brand, vendor storefront) takes filters from its query
string: `category`, one key per filterable taxonomy (the taxonomy's slug in the page language),
`vendor`, `price_min`, `price_max`, and `page`. A category or term value is its slug path
from the top of its tree (`parent/child`, the D-07 URL shape): slugs are unique among siblings
only, so a bare child slug would be ambiguous. This module parses and normalises them, and
writes them back in one canonical order, so a filtered or paginated listing has exactly one
indexable URL, built on the page's current URL (for a term, the URL its `url_mode` gives).

Invalid values are dropped and reported, never raised: an adapter compares the request with
`canonical_query` and redirects or marks the page noindex. The ORM side (slug resolution and the
queryset) lives in `services` (listing section).
"""

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from urllib.parse import parse_qs, parse_qsl, quote, urlencode

from .engine import to_price

CATEGORY = "category"
VENDOR = "vendor"
PRICE_MIN = "price_min"
PRICE_MAX = "price_max"
PAGE = "page"
# Keys a taxonomy slug can never take: they would shadow a filter.
RESERVED_KEYS = frozenset({CATEGORY, VENDOR, PRICE_MIN, PRICE_MAX, PAGE})

# Same shape as the stored URL slugs (catalog.taxonomy.url_slug_validator, vendor slugs).
_SEGMENT = r"[a-z0-9]+(?:[-_][a-z0-9]+)*"
_SLUG = re.compile(rf"^{_SEGMENT}$")
# A category or term: the slugs from the top of its tree, joined by "/".
_SLUG_PATH = re.compile(rf"^{_SEGMENT}(?:/{_SEGMENT})*$")
PATH_SEPARATOR = "/"
# Nine digits: far beyond any real listing, and int() never sees an oversized string.
_PAGE = re.compile(r"^[0-9]{1,9}$")


@dataclass(frozen=True)
class ListingParams:
    """Normalised listing filters: slugs, Decimal prices, the page number (1 = first page).

    `terms` maps a taxonomy key to its selected term slugs, both sorted, as nested tuples so the
    value is hashable and compares equal whatever order the query had.
    """

    category: str | None = None
    terms: tuple[tuple[str, tuple[str, ...]], ...] = ()
    vendor: str | None = None
    price_min: Decimal | None = None
    price_max: Decimal | None = None
    page: int = 1

    @property
    def has_price_filter(self) -> bool:
        return self.price_min is not None or self.price_max is not None

    @property
    def has_filters(self) -> bool:
        return bool(self.category or self.terms or self.vendor or self.has_price_filter)

    def term_slugs(self, key: str) -> tuple[str, ...]:
        return dict(self.terms).get(key, ())

    def with_page(self, page: int) -> "ListingParams":
        if page < 1:
            raise ValueError("The page must be 1 or more.")
        return replace(self, page=page)


@dataclass(frozen=True)
class ParsedParams:
    params: ListingParams
    errors: tuple[str, ...] = ()  # "key: reason", for values that were dropped
    ignored: tuple[str, ...] = ()  # keys that are not listing filters (tracking, other pages)


def _slug(value: str, pattern: re.Pattern[str] = _SLUG) -> str | None:
    slug = value.strip().lower()
    return slug if pattern.match(slug) else None


def slug_path(value: str) -> tuple[str, ...]:
    """The slugs of a category or term value, from the top of its tree."""
    return tuple(value.split(PATH_SEPARATOR))


def _amount(value: str) -> Decimal:
    if not value.strip():
        raise ValueError("empty")
    try:
        return to_price(value)
    except ArithmeticError as exc:  # e.g. an exponent too large to quantize
        raise ValueError(str(exc)) from exc


def parse_listing_params(
    query: Mapping[str, Sequence[str]], *, term_keys: Collection[str]
) -> ParsedParams:
    """Listing filters from a query (`dict(request.GET.lists())`).

    `term_keys` are the slugs of the filterable taxonomies in the page language. Single-value
    keys keep their first value; term keys take repeated values and comma-separated lists.
    Category and term values are slug paths (`parent/child`). A reversed price range is
    swapped.
    """
    clash = RESERVED_KEYS.intersection(term_keys)
    if clash:
        raise ValueError(f"A taxonomy key cannot be a listing parameter: {sorted(clash)}")
    errors: list[str] = []

    def single(key: str) -> str | None:
        values = query.get(key) or []
        if len(values) > 1:
            errors.append(f"{key}: only one value is allowed")
        return values[0] if values else None

    def slug_of(key: str, pattern: re.Pattern[str] = _SLUG) -> str | None:
        raw = single(key)
        if raw is None:
            return None
        slug = _slug(raw, pattern)
        if slug is None:
            errors.append(f"{key}: {raw!r} is not a valid slug")
        return slug

    def amount_of(key: str) -> Decimal | None:
        raw = single(key)
        if raw is None:
            return None
        try:
            return _amount(raw)
        except (TypeError, ValueError):
            errors.append(f"{key}: {raw!r} is not a valid amount")
            return None

    category = slug_of(CATEGORY, _SLUG_PATH)
    vendor = slug_of(VENDOR)
    price_min, price_max = amount_of(PRICE_MIN), amount_of(PRICE_MAX)
    if price_min is not None and price_max is not None and price_min > price_max:
        price_min, price_max = price_max, price_min

    page = 1
    raw_page = single(PAGE)
    if raw_page is not None:
        digits = raw_page.strip()
        if _PAGE.match(digits) and int(digits) >= 1:
            page = int(digits)
        else:
            errors.append(f"{PAGE}: {raw_page!r} is not a page number")

    terms: list[tuple[str, tuple[str, ...]]] = []
    for key in sorted(set(term_keys).intersection(query)):
        slugs: set[str] = set()
        for value in query[key]:
            for part in value.split(","):
                slug = _slug(part, _SLUG_PATH)
                if slug is None:
                    errors.append(f"{key}: {part!r} is not a valid slug")
                else:
                    slugs.add(slug)
        if slugs:
            terms.append((key, tuple(sorted(slugs))))

    ignored = tuple(sorted(set(query) - RESERVED_KEYS - set(term_keys)))
    params = ListingParams(
        category=category,
        terms=tuple(terms),
        vendor=vendor,
        price_min=price_min,
        price_max=price_max,
        page=page,
    )
    return ParsedParams(params=params, errors=tuple(errors), ignored=ignored)


def format_amount(amount: Decimal) -> str:
    """An amount as written in a URL: "10" for whole units, "9.50" otherwise."""
    if amount == amount.to_integral_value():
        return str(amount.quantize(Decimal(1)))
    return f"{amount:.2f}"


def canonical_pairs(params: ListingParams) -> list[tuple[str, str]]:
    """The query parameters in canonical order: category, taxonomies by key, vendor, price
    range, page. Page 1 and empty filters are left out."""
    pairs: list[tuple[str, str]] = []
    if params.category:
        pairs.append((CATEGORY, params.category))
    pairs.extend((key, ",".join(slugs)) for key, slugs in params.terms)
    if params.vendor:
        pairs.append((VENDOR, params.vendor))
    if params.price_min is not None:
        pairs.append((PRICE_MIN, format_amount(params.price_min)))
    if params.price_max is not None:
        pairs.append((PRICE_MAX, format_amount(params.price_max)))
    if params.page > 1:
        pairs.append((PAGE, str(params.page)))
    return pairs


def canonical_query(params: ListingParams) -> str:
    """The canonical query string, without the "?"; empty for an unfiltered first page."""
    return _encode(canonical_pairs(params))


def listing_url(base_path: str, params: ListingParams) -> str:
    """The indexable URL of a filtered or paginated listing on the page at `base_path`."""
    query = canonical_query(params)
    return f"{base_path}?{query}" if query else base_path


def query_to_lists(query: str) -> dict[str, list[str]]:
    """A query string as `parse_listing_params` takes it (the shape of `QueryDict.lists()`)."""
    return parse_qs(query, keep_blank_values=True)


def _encode(pairs: Sequence[tuple[str, str]]) -> str:
    return urlencode(pairs, safe=",/", quote_via=quote)


def split_query(query: str, *, term_keys: Collection[str]) -> tuple[str, str]:
    """A raw query string as (its listing part, everything else), each re-encoded in the order
    given. The listing part equals `canonical_query` exactly when the request is canonical; the
    rest (tracking parameters) is kept on a redirect but never enters the canonical URL."""
    keys = RESERVED_KEYS.union(term_keys)
    pairs = parse_qsl(query, keep_blank_values=True)
    listing = [(key, value) for key, value in pairs if key in keys]
    other = [(key, value) for key, value in pairs if key not in keys]
    return _encode(listing), _encode(other)


def join_query(path: str, *queries: str) -> str:
    query = "&".join(q for q in queries if q)
    return f"{path}?{query}" if query else path


# --- Robots (spec 10: noindex on pagination beyond a threshold and on empty results)

ROBOTS_INDEX = "index, follow"
ROBOTS_NOINDEX = "noindex, follow"
# Pages past this one stay reachable and followed but are not indexed.
INDEXED_PAGES = 10


def listing_robots(params: ListingParams, *, total: int, indexed_pages: int = INDEXED_PAGES) -> str:
    """`noindex, follow` for a page past `indexed_pages` and for a filtered listing with no
    products (like an internal search with no results); `index, follow` otherwise. Filtered
    listings with products are indexable on their canonical URL (CAT.8)."""
    if params.page > indexed_pages or (params.has_filters and total == 0):
        return ROBOTS_NOINDEX
    return ROBOTS_INDEX


# --- Pagination


@dataclass(frozen=True)
class PageWindow:
    page: int
    per_page: int
    total: int
    page_count: int  # at least 1: an empty listing still has its first page

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.per_page

    @property
    def limit(self) -> int:
        return self.per_page

    @property
    def out_of_range(self) -> bool:
        """A page past the last one: the adapter answers 404."""
        return self.page > self.page_count

    @property
    def has_previous(self) -> bool:
        return 1 < self.page <= self.page_count

    @property
    def has_next(self) -> bool:
        return self.page < self.page_count

    @property
    def previous_page(self) -> int | None:
        return self.page - 1 if self.has_previous else None

    @property
    def next_page(self) -> int | None:
        return self.page + 1 if self.has_next else None


def page_window(*, total: int, page: int, per_page: int) -> PageWindow:
    if total < 0 or page < 1 or per_page < 1:
        raise ValueError("total must be >= 0, page and per_page must be >= 1")
    page_count = max(1, -(-total // per_page))
    return PageWindow(page=page, per_page=per_page, total=total, page_count=page_count)
