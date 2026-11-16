"""The URLs of a language's sitemap (I18N.6, spec 09 §9.2, spec 10 technical SEO).

Only published, indexable pages that have their own text in the language: CMS pages (the
translations of Wagtail's i18n, events included: an event has no fallback copy), products,
categories, taxonomy terms, brands and vendor storefronts. A fallback copy is left out
(`sitemap_engine.sitemap_path`).
"""

import datetime as dt
from collections.abc import Iterator, Sequence

from django.db.models import Q
from django.http import HttpRequest
from wagtail.models import Page, Site

from apps.catalog import listings, slugs
from apps.catalog import services as catalog
from apps.catalog.models import (
    Brand,
    Category,
    ProductStatus,
    ProductTranslation,
    Taxonomy,
    Term,
)
from apps.cms.models import TermLandingPage
from apps.core import translations
from apps.core.i18n import primary_language
from apps.core.site_url import absolute_url
from apps.events.models import EventPage
from apps.vendors.models import Vendor

from .sitemap_engine import Entry, sitemap_path


def _absolute(request: HttpRequest, path: str | None) -> str | None:
    return absolute_url(request, path) if path else None


def _date(value: dt.datetime | None) -> dt.date | None:
    return value.date() if value is not None else None


def page_is_noindex(page: Page) -> bool:
    """The page's own robots rule, the one its template prints: a term landing page is noindex
    (its term page is the indexed one). The environment floor and the site's switch are checked
    once for the whole sitemap (the views answer 404 when indexing is off)."""
    return isinstance(page, TermLandingPage)


def _cms_entries(language: str, request: HttpRequest, *, events: bool) -> Iterator[Entry]:
    site = Site.find_for_request(request)
    pages = Page.objects.live().public().filter(locale__language_code=language, depth__gte=2)
    if site is not None:  # this site's tree only: another site's pages are not ours to list
        # The root page and its translations: each language has its own tree.
        trees = Q()
        for root in site.root_page.get_translations(inclusive=True):
            trees |= Q(path__startswith=root.path, depth__gte=root.depth)
        pages = pages.filter(trees)
    for page in pages.order_by("path").specific().iterator(chunk_size=500):
        if isinstance(page, EventPage) != events or page_is_noindex(page):
            continue
        visible = getattr(page, "is_publicly_visible", None)
        if visible is not None and not visible():  # a hidden event
            continue
        url = _absolute(request, page.get_url(request))
        if url:
            yield Entry(url, _date(page.last_published_at), page.title)


def cms_entries(language: str, request: HttpRequest) -> Iterator[Entry]:
    """The CMS pages other than events."""
    return _cms_entries(language, request, events=False)


def event_entries(language: str, request: HttpRequest) -> Iterator[Entry]:
    return _cms_entries(language, request, events=True)


def product_entries(language: str, request: HttpRequest) -> Iterator[Entry]:
    rows = ProductTranslation.objects.filter(
        language=language,
        status=translations.PUBLISHED,
        slug__gt="",
        product__status=ProductStatus.PUBLISHED,
        product__vendor__status=Vendor.Status.ACTIVE,
    ).order_by("product_id")
    values = rows.values_list("slug", "product__updated_at", "title")
    for slug, updated, title in values.iterator(chunk_size=1000):
        path = slugs.product_path(language, slug, default_language=primary_language())
        yield Entry(absolute_url(request, path), _date(updated), title)


def _listed_ids() -> tuple[set[int], set[int], set[str], set[str]]:
    """With ONE aggregate pass over the listed products: the vendor and brand ids that have
    some, and the tree paths of the categories and terms they sit in (a listing includes its
    subtree, so a node has products when some path starts with its own)."""
    listed = catalog.listed_products().order_by()
    vendor_ids = set(listed.values_list("vendor_id", flat=True).distinct())
    brand_ids = {
        pk for pk in listed.values_list("brand_id", flat=True).distinct() if pk is not None
    }
    category_paths = set(listed.values_list("category__path", flat=True).distinct()) | set(
        listed.values_list("secondary_categories__path", flat=True).distinct()
    )
    term_paths = set(listed.values_list("terms__path", flat=True).distinct())
    return (
        vendor_ids,
        brand_ids,
        {p for p in category_paths if p},
        {p for p in term_paths if p},
    )


def _has_path(paths: set[str], node_path: str) -> bool:
    return any(path.startswith(node_path) for path in paths)


def _listing_robots_allows() -> bool:
    """The canonical first page of a listing is indexable (the rule of the listing layout); it
    does not depend on which listing it is."""
    robots = listings.listing_robots(listings.ListingParams(), total=1)
    return not robots.startswith("noindex")


def _listing_entries(
    scopes: Sequence[catalog.ListingScope], language: str, request: HttpRequest, *, titles: bool
) -> Iterator[Entry]:
    """`titles` adds each listing's name (a query per listing: only `llms.txt` asks for it)."""
    if not _listing_robots_allows():
        return
    for scope, found in zip(scopes, catalog.listing_page_paths(scopes), strict=True):
        if found is None:
            continue
        paths, published = found
        path = sitemap_path(paths, published, language)
        if not path:
            continue
        anchor = catalog.scope_anchor(scope, language) if titles else None
        yield Entry(absolute_url(request, path), title=anchor.name if anchor else "")


def listing_entries(
    language: str, request: HttpRequest, *, titles: bool = False
) -> Iterator[Entry]:
    """Category, taxonomy term and brand listings."""
    _vendor_ids, brand_ids, category_paths, term_paths = _listed_ids()
    scopes: list[catalog.ListingScope] = [
        catalog.ListingScope(category=row)
        for row in Category.objects.order_by("path")
        if _has_path(category_paths, row.path)
    ]
    for taxonomy in Taxonomy.objects.filter(has_landing=True).order_by("code"):
        scopes += [
            catalog.ListingScope(term=row)
            for row in Term.objects.filter(taxonomy=taxonomy).order_by("path")
            if _has_path(term_paths, row.path)
        ]
    scopes += [
        catalog.ListingScope(brand=row)
        for row in Brand.objects.filter(status=Brand.Status.APPROVED, pk__in=brand_ids)
    ]
    yield from _listing_entries(scopes, language, request, titles=titles)


def vendor_entries(language: str, request: HttpRequest, *, titles: bool = False) -> Iterator[Entry]:
    """The storefronts of the active vendors that have listed products."""
    vendor_ids = _listed_ids()[0]
    scopes = [
        catalog.ListingScope(vendor=row)
        for row in Vendor.objects.filter(status=Vendor.Status.ACTIVE, pk__in=vendor_ids).order_by(
            "id"
        )
    ]
    yield from _listing_entries(scopes, language, request, titles=titles)


def kind_entries(
    kind: str, language: str, request: HttpRequest, *, titles: bool = False
) -> Iterator[Entry]:
    """The URLs of one type in one language (an unknown `kind` is a programming error)."""
    if kind == "listings":
        return listing_entries(language, request, titles=titles)
    if kind == "vendors":
        return vendor_entries(language, request, titles=titles)
    builders = {"pages": cms_entries, "events": event_entries, "products": product_entries}
    return builders[kind](language, request)
