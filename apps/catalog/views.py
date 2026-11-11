"""Listing pages: category, taxonomy term, brand and vendor storefront (CAT.8, spec 03 §3.2 URLs,
owner decision D-07). Thin adapters over `services` (the anchor and the listing query) and
`listings` (canonical query, robots, page links).

One URL per listing: a request in another language's slugs or segment, or with a query that is
not canonical, gets a single 301 to the canonical URL (tracking parameters kept). A page past the
last one and a filter value that matches nothing answer 404. A language without a published text
shows the fallback, with the source-language page as canonical and `hreflang` listing only the
published languages (spec 09).

An editorial landing page (CMS.4) adds its content to the first unfiltered page of its listing,
through the `listings.EDITORIAL_HOOK` Wagtail hook: the page lives on the listing's URL and adds
no second one (D-07).
"""

from decimal import Decimal
from typing import Any

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse, HttpResponsePermanentRedirect
from django.http.response import HttpResponseBase
from django.shortcuts import render
from django.utils import translation
from django.views.decorators.http import require_safe
from wagtail import hooks
from wagtail.rich_text import RichText
from wagtail.views import serve

from apps.core import translations
from apps.core.i18n import hreflang_alternates
from apps.core.money import CURRENCY
from apps.vendors import services as vendors_services

from . import engine, listings, services, slugs

TEMPLATE = "catalog/listing.html"


def _language() -> str:
    return translation.get_language() or settings.LANGUAGE_CODE


@require_safe
def category_listing(request: HttpRequest, segment: str, slugs: str) -> HttpResponseBase:
    language = _language()
    return _listing(request, services.category_anchor(slugs, language), language)


def term_listing(request: HttpRequest, taxonomy: str, terms: str) -> HttpResponseBase:
    """A term page, or the CMS page at the same path when there is no such term page.

    A term route has no fixed segment, so a CMS page whose first segment equals a taxonomy
    slug must stay reachable: a path that names no term page is handed to the Wagtail page
    lookup, whatever the request method.
    """
    language = _language()
    anchor = services.term_anchor(taxonomy, terms, language)
    if anchor is None or language not in anchor.paths:
        response: HttpResponseBase = serve(request, f"{taxonomy}/{terms}/")
        return response
    return _safe_listing(request, anchor, language)


@require_safe
def brand_listing(request: HttpRequest, segment: str, slug: str) -> HttpResponseBase:
    language = _language()
    return _listing(request, services.brand_anchor(slug, language), language)


@require_safe
def vendor_listing(request: HttpRequest, segment: str, slug: str) -> HttpResponseBase:
    language = _language()
    return _listing(request, services.vendor_anchor(slug, language), language)


def _path_language(anchor: services.ListingAnchor, path: str, language: str) -> str | None:
    """The language whose slugs and segment `path` (under the `language` prefix) is written
    in: `language` itself when it matches, else another one; None for a mixed path."""
    default = settings.LANGUAGE_CODE
    own = path.removeprefix(slugs.language_prefix(language, default_language=default))
    for lang in dict.fromkeys([language, *anchor.paths]):
        written = anchor.paths.get(lang, "")
        if written.removeprefix(slugs.language_prefix(lang, default_language=default)) == own:
            return lang
    return None


def _foreign_redirect(
    request: HttpRequest, anchor: services.ListingAnchor, language: str, slug_language: str
) -> HttpResponseBase:
    """The 301 for a URL in `slug_language`'s slugs: its filters (keys and slug paths of that
    language) are re-expressed in the page language; one with no equivalent is dropped."""
    query = request.META.get("QUERY_STRING", "")
    foreign_keys = services.listing_term_keys(slug_language).keys()
    own_keys = services.listing_term_keys(language).keys()
    foreign = listings.parse_listing_params(dict(request.GET.lists()), term_keys=foreign_keys)
    params = services.listing_params_in(foreign.params, slug_language, language)
    if params is None:
        params = listings.ListingParams(
            vendor=foreign.params.vendor,
            price_min=foreign.params.price_min,
            price_max=foreign.params.price_max,
            page=foreign.params.page,
        )
    _given, other = listings.split_query(query, term_keys={*foreign_keys, *own_keys})
    location = anchor.paths[language]
    return HttpResponsePermanentRedirect(
        listings.join_query(location, listings.canonical_query(params), other)
    )


def _editorial(
    request: HttpRequest, scope: services.ListingScope, language: str
) -> listings.ListingEditorial | None:
    """The first editorial content a registered hook gives for this listing, if any."""
    for hook in hooks.get_hooks(listings.EDITORIAL_HOOK):
        found: listings.ListingEditorial | None = hook(request, scope, language)
        if found is not None:
            return found
    return None


def _listing(
    request: HttpRequest, anchor: services.ListingAnchor | None, language: str
) -> HttpResponseBase:
    if anchor is None or language not in anchor.paths:
        raise Http404("No such listing.")
    location = anchor.paths[language]
    if request.path_info != location:
        slug_language = _path_language(anchor, request.path_info, language)
        if slug_language is not None and slug_language != language:
            return _foreign_redirect(request, anchor, language, slug_language)
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

    # The object in each language it is published in: the one source of the `hreflang` links
    # and of the language switcher (`language_urls`, read by the `language_links` tag).
    language_urls: dict[str, str] = {}
    for lang in anchor.published:
        lang_params = in_language(lang)
        if lang in anchor.paths and lang_params is not None:
            language_urls[lang] = listings.listing_url(anchor.paths[lang], lang_params)
    alternates = hreflang_alternates(
        {lang: request.build_absolute_uri(url) for lang, url in language_urls.items()}
    )
    source = translations.source_language()

    canonical_url = absolute(language, params)
    if anchor.is_fallback(language) and source in anchor.paths:
        source_params = in_language(source) or listings.ListingParams(page=params.page)
        canonical_url = absolute(source, source_params)

    groups = services.listing_filter_groups(anchor.scope, params, language)
    vendors = services.listing_vendor_options(anchor.scope, params)
    store_categories = services.store_categories(anchor.scope, params, language)
    vendor = anchor.scope.vendor
    vendor_profile = vendors_services.storefront_profile(vendor, language) if vendor else None
    labels = {
        (group.key, option.value): option.label for group in groups for option in group.options
    }
    labels.update({(listings.VENDOR, option.value): option.label for option in vendors})
    brand = anchor.scope.brand
    site = request.build_absolute_uri("/").rstrip("/")

    context: dict[str, Any] = {
        "editorial": (
            _editorial(request, anchor.scope, language)
            if listings.shows_editorial(params)
            else None
        ),
        "anchor": anchor,
        "location": location,
        "cards": services.product_cards(page.products, language),
        "currency": CURRENCY,
        "filter_groups": groups,
        "carried_filters": listings.carried_filters(
            params, {(group.key, option.value) for group in groups for option in group.options}
        ),
        "vendor_options": vendors,
        "store_categories": store_categories,
        "vendor_profile": vendor_profile,
        "brand_profile": services.brand_profile(brand, language) if brand else None,
        # One JSON-LD block per page (SEO.7): the brand page's graph until `seo.page_graph`.
        "jsonld": (
            engine.jsonld_text(services.brand_graph(brand, language, site=site, paths=anchor.paths))
            if brand
            else ""
        ),
        "policy_items": [
            {"title": title, "content": RichText(text)}
            for title, text in (vendor_profile.policies if vendor_profile else ())
        ],
        "price_min": _amount(params.price_min),
        "price_max": _amount(params.price_max),
        "chips": listings.filter_chips(params, location, labels),
        "clear_url": location,
        "page_links": listings.page_links(params, location, window)
        if window.page_count > 1
        else [],
        "scope": anchor.scope,
        "name": anchor.name,
        "products": page.products,
        "params": params,
        "window": window,
        "is_fallback": anchor.is_fallback(language),
        "canonical_url": canonical_url,
        "alternates": alternates,
        "language_urls": language_urls,
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


def _amount(amount: Decimal | None) -> str:
    return listings.format_amount(amount) if amount is not None else ""


_safe_listing = require_safe(_listing)
