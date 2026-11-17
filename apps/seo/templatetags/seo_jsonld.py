"""`{% page_graph_jsonld %}`: the one JSON-LD block of a public page (SEO.7; SEO.1 for products).

The only writer of the block: the graph is JSON escaped like Django's `json_script` (`<`, `>`,
`&`, U+2028, U+2029), so staff- and vendor-entered text cannot close the element.
"""

from typing import Any

from django import template
from django.http import HttpRequest
from django.template.context import Context
from django.utils.html import format_html
from django.utils.safestring import SafeString, mark_safe

from apps.catalog.product_pages import ProductPage
from apps.core.site_url import site_root_url
from apps.seo import product_jsonld as jsonld
from apps.seo.page_graph import graph_text, page_graph

register = template.Library()


@register.simple_tag(takes_context=True)
def product_jsonld(context: Context, page: ProductPage) -> SafeString:
    request = context["request"]
    site = site_root_url(request)
    return format_html(
        '<script type="application/ld+json">{}</script>',
        SafeString(product_jsonld_text(page, site, request)),
    )


def product_jsonld_text(page: ProductPage, site: str, request: HttpRequest | None = None) -> str:
    return jsonld.graph_text(page, site=site, request=request)


@register.simple_tag(takes_context=True)
def page_graph_jsonld(context: Context) -> SafeString:
    """The page's single `<script type="application/ld+json">` from `seo.page_graph`; "" for a
    page type without a graph (not public) or without a request (the 500 page).

    The object is the view's `graph_object`, the product page's `page_data` or the CMS `page`."""
    request = context.get("request")
    if request is None:
        return mark_safe("")
    obj = context.get("graph_object") or context.get("page_data") or context.get("page")
    if obj is None:
        return mark_safe("")
    flat: dict[str, Any] = {str(k): v for k, v in context.flatten().items()}
    graph = page_graph(request, obj, context=flat)
    if graph is None:
        return mark_safe("")
    return format_html(
        '<script type="application/ld+json">{}</script>', SafeString(graph_text(graph))
    )
