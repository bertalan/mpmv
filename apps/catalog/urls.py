"""Public catalog routes, mounted inside `i18n_patterns` before the CMS pages (CAT.8, D-07).

Each fixed segment answers in every spelling (`/it/brand/…` finds the brand page, which then
redirects to `/it/marchio/…`). A taxonomy page has no fixed segment: its first segment matches
only the slug of a taxonomy with term pages, in some language, so every other path still
reaches the CMS page lookup. The catalog segments come first and can never be a taxonomy slug.
"""

import re

from django.urls import path, re_path, register_converter

from . import services, slugs, views


class LandingTaxonomyConverter:
    """A path segment that is the slug of a taxonomy with term pages (otherwise no match)."""

    regex = r"[a-z0-9]+(?:-[a-z0-9]+)*"

    def to_python(self, value: str) -> str:
        if value in slugs.RESERVED_SEGMENTS or not services.landing_taxonomy_slug_exists(value):
            raise ValueError(f"{value!r} is not a taxonomy with term pages")
        return value

    def to_url(self, value: str) -> str:
        return value


register_converter(LandingTaxonomyConverter, "landing_taxonomy")


def _segments(segments: dict[str, str]) -> str:
    return "|".join(re.escape(segment) for segment in sorted(set(segments.values())))


SLUG_PATH = r"[a-z0-9][a-z0-9/_-]*"

app_name = "catalog"
urlpatterns = [
    re_path(
        rf"^(?P<segment>{_segments(slugs.CATEGORY_SEGMENTS)})/(?P<slugs>{SLUG_PATH})/$",
        views.category_listing,
        name="category",
    ),
    re_path(
        rf"^(?P<segment>{_segments(slugs.BRAND_SEGMENTS)})/(?P<slug>[-a-z0-9_]+)/$",
        views.brand_listing,
        name="brand",
    ),
    re_path(
        rf"^(?P<segment>{_segments(slugs.SHOP_SEGMENTS)})/(?P<slug>[-a-zA-Z0-9_]+)/$",
        views.vendor_listing,
        name="vendor",
    ),
    path("<landing_taxonomy:taxonomy>/<path:terms>/", views.term_listing, name="term"),
]
