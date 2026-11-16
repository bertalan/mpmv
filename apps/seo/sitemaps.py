"""The URLs of a language's sitemap (I18N.6, spec 09 §9.2, spec 10 technical SEO).

Only published, indexable pages that have their own text in the language: CMS pages (the
translations of Wagtail's i18n, events included: an event has no fallback copy), products,
categories, taxonomy terms, brands and vendor storefronts. A fallback copy is left out
(`sitemap_engine.sitemap_path`).
"""

import datetime as dt
from collections.abc import Iterator

from django.http import HttpRequest
from wagtail.models import Page

from apps.catalog import services as catalog
from apps.catalog import slugs
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
from apps.vendors.models import Vendor

from .sitemap_engine import Entry, sitemap_path


def _absolute(request: HttpRequest, path: str | None) -> str | None:
    return request.build_absolute_uri(path) if path else None


def _date(value: dt.datetime | None) -> dt.date | None:
    return value.date() if value is not None else None


def cms_entries(language: str, request: HttpRequest) -> Iterator[Entry]:
    pages = Page.objects.live().public().filter(locale__language_code=language, depth__gte=2)
    for page in pages.order_by("path").specific():
        if isinstance(page, TermLandingPage):  # noindex: its term page is the indexed one
            continue
        visible = getattr(page, "is_publicly_visible", None)
        if visible is not None and not visible():  # a hidden event
            continue
        url = _absolute(request, page.get_url(request))
        if url:
            yield Entry(url, _date(page.last_published_at))


def product_entries(language: str, request: HttpRequest) -> Iterator[Entry]:
    rows = ProductTranslation.objects.filter(
        language=language,
        status=translations.PUBLISHED,
        slug__gt="",
        product__status=ProductStatus.PUBLISHED,
        product__vendor__status=Vendor.Status.ACTIVE,
    ).order_by("product_id")
    for slug in rows.values_list("slug", flat=True):
        path = slugs.product_path(language, slug, default_language=primary_language())
        yield Entry(request.build_absolute_uri(path))


def _listing_entries(
    scopes: list[catalog.ListingScope], language: str, request: HttpRequest
) -> Iterator[Entry]:
    for scope in scopes:
        anchor = catalog.scope_anchor(scope, language)
        if anchor is None:
            continue
        path = sitemap_path(anchor.paths, anchor.published, language)
        if path and catalog.listing_products(scope).exists():
            yield Entry(request.build_absolute_uri(path))


def listing_entries(language: str, request: HttpRequest) -> Iterator[Entry]:
    scopes = [catalog.ListingScope(category=row) for row in Category.objects.order_by("path")]
    for taxonomy in Taxonomy.objects.filter(has_landing=True).order_by("code"):
        scopes += [
            catalog.ListingScope(term=row)
            for row in Term.objects.filter(taxonomy=taxonomy).order_by("path")
        ]
    scopes += [
        catalog.ListingScope(brand=row)
        for row in Brand.objects.filter(status=Brand.Status.APPROVED)
    ]
    scopes += [
        catalog.ListingScope(vendor=row)
        for row in Vendor.objects.filter(status=Vendor.Status.ACTIVE).order_by("id")
    ]
    yield from _listing_entries(scopes, language, request)


def language_entries(language: str, request: HttpRequest) -> list[Entry]:
    return [
        *cms_entries(language, request),
        *listing_entries(language, request),
        *product_entries(language, request),
    ]
