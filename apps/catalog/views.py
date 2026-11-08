"""Listing pages: category, taxonomy term, brand and vendor storefront (CAT.8, spec 03 §3.2 URLs,
owner decision D-07). Thin adapters over `services` (the anchor and the listing query) and
`listings` (canonical query, robots, page links).

One URL per listing: a request in another language's slugs or segment, or with a query that is
not canonical, gets a single 301 to the canonical URL (tracking parameters kept). A page past the
last one and a filter value that matches nothing answer 404. A language without a published text
shows the fallback, with the source-language page as canonical and `hreflang` listing only the
published languages (spec 09).
"""

from typing import Any

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse, HttpResponsePermanentRedirect
from django.http.response import HttpResponseBase
from django.shortcuts import render
from django.utils import translation
from django.views.decorators.http import require_safe

from apps.core import translations

from . import listings, services

TEMPLATE = "catalog/listing.html"


def _language() -> str:
    return translation.get_language() or settings.LANGUAGE_CODE


@require_safe
def category_listing(request: HttpRequest, segment: str, slugs: str) -> HttpResponseBase:
    language = _language()
    return _listing(request, services.category_anchor(slugs, language), language)


@require_safe
def term_listing(request: HttpRequest, taxonomy: str, terms: str) -> HttpResponseBase:
    language = _language()
    return _listing(request, services.term_anchor(taxonomy, terms, language), language)


@require_safe
def brand_listing(request: HttpRequest, segment: str, slug: str) -> HttpResponseBase:
    language = _language()
    return _listing(request, services.brand_anchor(slug, language), language)


@require_safe
def vendor_listing(request: HttpRequest, segment: str, slug: str) -> HttpResponseBase:
    language = _language()
    return _listing(request, services.vendor_anchor(slug, language), language)


def _listing(
    request: HttpRequest, anchor: services.ListingAnchor | None, language: str
) -> HttpResponseBase:
    if anchor is None or language not in anchor.paths:
        raise Http404("No such listing.")
    location = anchor.paths[language]
    term_keys = services.listing_term_keys(language).keys()
    parsed = listings.parse_listing_params(dict(request.GET.lists()), term_keys=term_keys)
    params = parsed.params
    canonical_query = listings.canonical_query(params)
    given, other = listings.split_query(request.META.get("QUERY_STRING", ""), term_keys=term_keys)
    if request.path_info != location or given != canonical_query:
        return HttpResponsePermanentRedirect(listings.join_query(location, canonical_query, other))

    page = services.listing_page(anchor.scope, params, language, per_page=services.LISTING_PER_PAGE)
    if page.unknown or page.window.out_of_range:
        raise Http404("No such listing page.")
    window = page.window

    def absolute(lang: str, lang_params: listings.ListingParams) -> str:
        return request.build_absolute_uri(listings.listing_url(anchor.paths[lang], lang_params))

    def in_language(lang: str) -> listings.ListingParams | None:
        return services.listing_params_in(params, language, lang)

    alternates: list[tuple[str, str]] = []
    for lang in anchor.published:
        lang_params = in_language(lang)
        if lang in anchor.paths and lang_params is not None:
            alternates.append((lang, absolute(lang, lang_params)))
    source = translations.source_language()
    alternates.extend(("x-default", url) for lang, url in list(alternates) if lang == source)

    canonical_url = absolute(language, params)
    if anchor.is_fallback(language) and source in anchor.paths:
        source_params = in_language(source) or listings.ListingParams(page=params.page)
        canonical_url = absolute(source, source_params)

    context: dict[str, Any] = {
        "anchor": anchor,
        "scope": anchor.scope,
        "name": anchor.name,
        "products": page.products,
        "params": params,
        "window": window,
        "is_fallback": anchor.is_fallback(language),
        "canonical_url": canonical_url,
        "alternates": alternates,
        "robots": listings.listing_robots(params, total=window.total),
        "previous_url": (
            listings.listing_url(location, params.with_page(window.previous_page))
            if window.previous_page
            else None
        ),
        "next_url": (
            listings.listing_url(location, params.with_page(window.next_page))
            if window.next_page
            else None
        ),
    }
    response: HttpResponse = render(request, TEMPLATE, context)
    return response
