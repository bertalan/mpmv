"""The page meta fallback chain (SEO.8 2/2, D-36): page, then site SEO settings, then operator.

Pure functions, no Django. They mirror the `get_meta_*` chain of the wagtail-seo package.
"""

from dataclasses import dataclass

from apps.core.text import meta_description


@dataclass(frozen=True)
class PageMeta:
    title: str
    description: str
    image: str  # an absolute URL, or "" when no image is known


def first_text(*values: str | None) -> str:
    """The first value that is not empty after trimming."""
    for value in values:
        if value and value.strip():
            return value.strip()
    return ""


def resolve_meta(
    *,
    page_title: str,
    seo_title: str = "",
    search_description: str = "",
    summary: str = "",
    page_seo_image: str = "",
    page_image: str = "",
    site_image: str = "",
    operator_image: str = "",
) -> PageMeta:
    """Title: SEO title, else page title. Description: search description, else the page's own
    summary. Image: the page's SEO image, its main image, the site default, the operator logo."""
    return PageMeta(
        title=first_text(seo_title, page_title),
        description=meta_description(search_description, summary),  # SEO.17: plain, ≤160
        image=first_text(page_seo_image, page_image, site_image, operator_image),
    )
