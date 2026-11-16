"""`{% seo_links %}`: the canonical and `hreflang` links of a page, once, for every layout (I18N.6).

The base layout prints it in the head, so the ride theme and any page type get the same links.
A view that knows its object (product, listing, vendor storefront) puts `canonical_url` and
`alternates` (`apps.core.i18n.hreflang_alternates`) in the context; for a CMS page they come from
its live translations, the map the language switcher reads (`services.page_language_urls`).
`hreflang` lists only languages with a published translation; a fallback copy passes its source
page as `canonical_url`.
"""

from collections.abc import Iterable

from django import template
from django.template.context import Context
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString
from wagtail.models import Page

from apps.cms import services
from apps.core.i18n import hreflang_alternates

register = template.Library()


def _page_links(context: Context) -> tuple[str, Iterable[tuple[str, str]]]:
    """The links of a CMS page, on its Wagtail site's root URL: never the request's Host header."""
    request = context.get("request")
    page: Page | None = context.get("page")
    site = page.get_site() if page is not None else None
    if request is None or page is None or site is None or getattr(request, "is_preview", False):
        return "", ()

    def absolute(url: str) -> str:
        return url if "://" in url else site.root_url + url

    own = page.get_url(request)
    found = {
        code: absolute(url) for code, url in services.page_language_urls(page, request).items()
    }
    return (absolute(own) if own else ""), hreflang_alternates(found)


@register.simple_tag(takes_context=True)
def seo_links(context: Context) -> SafeString:
    canonical = context.get("canonical_url")
    alternates = context.get("alternates")
    if canonical is None and alternates is None:
        canonical, alternates = _page_links(context)
    parts = []
    if canonical:
        parts.append(format_html('<link rel="canonical" href="{}">', canonical))
    if alternates:
        parts.append(
            format_html_join("\n  ", '<link rel="alternate" hreflang="{}" href="{}">', alternates)
        )
    return format_html_join("\n  ", "{}", ((part,) for part in parts))
