"""The public search results page (CAT.27, owner decision 2026-11-17).

The legacy URL `/?s=<query>` (and `/<lang>/?s=`) answers on the language home: `cms.HomePage.serve`
hands the request to `search_view`. Products come first (CAT.9 search with the CAT.8 listing
filters and numbered pagination), then short groups of other content, each with a see-all link
(`?s=<query>&in=<group>`). Events and articles come from apps above or beside the catalog through
the `SEARCH_GROUPS_HOOK` Wagtail hook (the catalog never imports them); the vendors group is here.

The page is a plain GET form that works without JavaScript, always `noindex, follow` and never
cached (a response per query must not fill a shared cache). The query is cut to
`QUERY_MAX_LENGTH` characters and each client is limited to `settings.SEARCH_RATE_LIMIT` searches
a minute; an empty query shows the form only and costs no slot.
"""

import datetime as dt
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from django.conf import settings
from django.db.models import Case, Q, Value, When
from django.http import Http404, HttpRequest, HttpResponse
from django.http.response import HttpResponseBase
from django.shortcuts import render
from django.utils import translation
from django.utils.cache import add_never_cache_headers
from django.utils.translation import gettext_lazy as _
from wagtail import hooks

from apps.core import translations
from apps.core.http import client_ip
from apps.core.money import CURRENCY
from apps.core.services import consume_rate_limit
from apps.core.throttling import ip_bucket
from apps.vendors import services as vendors_services

from . import listings, services, slugs
from .models import CategoryTranslation, Product

TEMPLATE = "catalog/search.html"
SEARCH_PARAMETER = services.SEARCH_PARAMETER
GROUP_PARAMETER = "in"  # the see-all link of a group: only that group, longer
QUERY_MAX_LENGTH = 100
RATE_WINDOW = dt.timedelta(minutes=1)
# The keys of the groups below the products: events and articles come from hooks, vendors from here.
KNOWN_GROUPS = frozenset({"events", "articles", "vendors"})
GROUP_LIMIT = 5  # items a group shows below the products
GROUP_ALL_LIMIT = 50  # items a group shows on its own see-all page
SUGGESTION_LIMIT = 8
# `fn(request, query, language, limit)` returns a `SearchGroup` or None; it fetches `limit + 1`
# items at most, so that `more` says whether a see-all link is worth showing.
SEARCH_GROUPS_HOOK = "catalog_search_groups"


@dataclass(frozen=True)
class SearchItem:
    title: str
    url: str
    summary: str = ""
    language: str = ""  # set when the item is shown in a language other than the page's


@dataclass(frozen=True)
class SearchGroup:
    """A short list of matches of one kind. `order` sorts the groups below the products."""

    key: str
    title: str
    items: Sequence[SearchItem]
    order: int
    more: bool = False  # there are matches beyond `items`
    see_all_url: str = field(default="", compare=False)

    def with_see_all(self, url: str) -> "SearchGroup":
        return SearchGroup(self.key, self.title, self.items, self.order, self.more, url)


@dataclass(frozen=True)
class ProductResults:
    products: list[Product]
    window: listings.PageWindow


def clean_query(raw: str) -> str:
    """The visitor's query: whitespace collapsed, control characters dropped, cut to the limit."""
    printable = "".join(char for char in raw if char.isprintable() or char.isspace())
    return " ".join(printable.split())[:QUERY_MAX_LENGTH].strip()


def search_url(path: str, query: str, params: listings.ListingParams, **extra: str) -> str:
    """The URL of a results page: the query first, then the group, then the filters."""
    pairs = [(SEARCH_PARAMETER, query), *extra.items()]
    return listings.join_query(path, urlencode(pairs), listings.canonical_query(params))


# --- Products


def _exact_identifier_pks(query: str) -> list[int]:
    """Listed products whose GTIN or MPN is exactly the query (no spaces: an identifier)."""
    if " " in query or len(query) < 3:
        return []
    match = Q(mpn__iexact=query)
    if query.isdigit():
        match |= Q(gtin=query)
    pks = services.listed_products().filter(match).order_by("pk").values_list("pk", flat=True)
    return list(pks[:20])


def product_results(
    query: str, language: str, params: listings.ListingParams, *, per_page: int
) -> ProductResults:
    """One page of the listed products matching `query` that pass the listing filters, best
    first: an exact GTIN or MPN, then the CAT.9 ranking. A filter value that names nothing
    matches nothing."""
    ranked = list(services.search(query, language).values_list("pk", flat=True))
    exact = _exact_identifier_pks(query)
    order = [*exact, *(pk for pk in ranked if pk not in exact)]
    resolved = services.resolve_listing_filter(params, language)
    products = services.listing_products(services.NO_SCOPE, resolved.filters)
    if order:
        position = Case(*(When(pk=pk, then=Value(i)) for i, pk in enumerate(order)))
        products = products.filter(pk__in=order).order_by(position)
    else:
        products = products.none()
    window = listings.page_window(total=products.count(), page=params.page, per_page=per_page)
    rows: list[Product] = []
    if not window.out_of_range:
        selected = products.select_related("vendor", "brand", "category")
        rows = list(selected[window.offset : window.offset + window.limit])
    return ProductResults(rows, window)


def category_suggestions(language: str) -> list[SearchItem]:
    """Top-level categories with a published name in `language`, for a search with no match."""
    rows = (
        CategoryTranslation.objects.filter(
            language=language,
            status=translations.PUBLISHED,
            slug__gt="",
            category__depth=1,
        )
        .order_by("name", "pk")
        .values_list("name", "slug")[:SUGGESTION_LIMIT]
    )
    default = settings.LANGUAGE_CODE
    return [
        SearchItem(title=name, url=slugs.category_path(language, [slug], default_language=default))
        for name, slug in rows
    ]


# --- Vendors


def vendors_group(request: HttpRequest, query: str, language: str, limit: int) -> SearchGroup:
    rows = list(
        vendors_services.storefront_vendors()
        .filter(display_name__icontains=query)
        .order_by("display_name", "pk")[: limit + 1]
    )
    default = settings.LANGUAGE_CODE
    items = [
        SearchItem(
            title=str(vendor.display_name),
            url=slugs.vendor_path(language, vendor.slug, default_language=default),
        )
        for vendor in rows[:limit]
    ]
    return SearchGroup("vendors", str(_("Vendors")), items, order=30, more=len(rows) > limit)


def groups(request: HttpRequest, query: str, language: str, *, only: str = "") -> list[SearchGroup]:
    """The non-empty groups below the products (all of them, or `only` one at full length)."""
    limit = GROUP_ALL_LIMIT if only else GROUP_LIMIT
    found: list[SearchGroup | None] = [
        hook(request, query, language, limit) for hook in hooks.get_hooks(SEARCH_GROUPS_HOOK)
    ]
    found.append(vendors_group(request, query, language, limit))
    shown = sorted((g for g in found if g is not None and g.items), key=lambda g: g.order)
    return [g for g in shown if not only or g.key == only]


# --- The view


def _rate_limited(request: HttpRequest) -> HttpResponse | None:
    wait = consume_rate_limit(
        f"search:ip:{ip_bucket(client_ip(request)) or 'unknown'}",
        limit=settings.SEARCH_RATE_LIMIT,
        window=RATE_WINDOW,
    )
    if not wait:
        return None
    response = HttpResponse(
        str(_("Too many searches, try again shortly.")),
        status=429,
        content_type="text/plain; charset=utf-8",
    )
    response["X-Robots-Tag"] = listings.ROBOTS_NOINDEX
    response["Retry-After"] = str(max(1, math.ceil(wait.total_seconds())))
    add_never_cache_headers(response)
    return response


def search_view(request: HttpRequest) -> HttpResponseBase:
    """The results page on the language home. Safe methods only (the caller checks)."""
    language = translation.get_language() or settings.LANGUAGE_CODE
    query = clean_query(request.GET.get(SEARCH_PARAMETER, ""))
    if query:
        blocked = _rate_limited(request)
        if blocked is not None:
            return blocked
    term_keys = services.listing_term_keys(language).keys()
    parsed = listings.parse_listing_params(dict(request.GET.lists()), term_keys=term_keys)
    params = parsed.params
    only = request.GET.get(GROUP_PARAMETER, "")
    if only not in KNOWN_GROUPS:
        only = ""  # an unknown group is ignored, never an empty page
    path = request.path
    results: ProductResults | None = None
    shown_groups: list[SearchGroup] = []
    groups_below: list[SearchGroup] = []
    if query:
        if not only:
            results = product_results(query, language, params, per_page=services.LISTING_PER_PAGE)
            if results.window.out_of_range:
                raise Http404("No such results page.")
        shown_groups = groups(request, query, language, only=only)
        groups_below = [g.with_see_all(search_url(path, query, params, **{GROUP_PARAMETER: g.key}))
                        if g.more and not only else g for g in shown_groups]  # fmt: skip
    window = results.window if results else None
    filter_groups = services.listing_filter_groups(services.NO_SCOPE, params, language)
    context: dict[str, Any] = {
        "query": query,
        "location": path,
        "only": only,
        "cards": services.product_cards(results.products, language) if results else [],
        "currency": CURRENCY,
        "window": window,
        "groups": groups_below,
        "suggestions": (
            category_suggestions(language)
            if query and not (results and results.products) and not groups_below
            else []
        ),
        "searched": bool(query),
        "filter_groups": filter_groups if query and not only else [],
        "carried_filters": [(SEARCH_PARAMETER, query)] if query and not only else [],
        "vendor_options": (
            services.listing_vendor_options(services.NO_SCOPE, params) if query and not only else []
        ),
        "price_min": listings.format_amount(params.price_min)
        if params.price_min is not None
        else "",
        "price_max": listings.format_amount(params.price_max)
        if params.price_max is not None
        else "",
        "page_links": _page_links(path, query, params, window) if window else [],
        "robots": listings.ROBOTS_NOINDEX,
        "previous_url": (
            search_url(path, query, params.with_page(window.previous_page))
            if window and window.previous_page
            else None
        ),
        "next_url": (
            search_url(path, query, params.with_page(window.next_page))
            if window and window.next_page
            else None
        ),
    }
    response: HttpResponse = render(request, TEMPLATE, context)
    add_never_cache_headers(response)
    return response


def _page_links(
    path: str, query: str, params: listings.ListingParams, window: listings.PageWindow
) -> list[listings.PageLink]:
    if window.page_count <= 1:
        return []
    return [
        listings.PageLink(None, None)
        if number is None
        else listings.PageLink(
            number, search_url(path, query, params.with_page(number)), current=number == window.page
        )
        for number in listings.page_numbers(window)
    ]
