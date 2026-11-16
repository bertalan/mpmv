"""`{% product_jsonld page_data %}`: the product page's one JSON-LD block (SEO.1).

The only writer of the block: the graph is JSON escaped like Django's `json_script` (`<`, `>`,
`&`, U+2028, U+2029), so staff- and vendor-entered text cannot close the element.
"""

from django import template
from django.template.context import Context
from django.utils.html import format_html
from django.utils.safestring import SafeString

from apps.catalog.product_pages import ProductPage
from apps.core.site_url import site_root_url
from apps.seo import product_jsonld as jsonld

register = template.Library()


@register.simple_tag(takes_context=True)
def product_jsonld(context: Context, page: ProductPage) -> SafeString:
    request = context["request"]
    site = site_root_url(request)
    return format_html(
        '<script type="application/ld+json">{}</script>',
        SafeString(product_jsonld_text(page, site)),
    )


def product_jsonld_text(page: ProductPage, site: str) -> str:
    return jsonld.graph_text(page, site=site)
