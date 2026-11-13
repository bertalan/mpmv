"""Robots meta for the base layout (SEO.8): one tag per page, noindex when forced.

Forced means the environment floor or the site's own SEO setting (off by default).
`{% robots_meta "noindex" %}` prints a page's own rule (replaced by `noindex, nofollow` when
indexing is forced) and records that it did. `{% default_robots_meta %}`, last in the layout's
head, prints the forced rule only when no page printed one, so a page never has two.
"""

from django import template
from django.template.context import Context
from django.utils.html import format_html
from django.utils.safestring import SafeString

from apps.core.indexing import NOINDEX, is_noindex

register = template.Library()
_FLAG = "seo_robots_printed"


@register.simple_tag(takes_context=True)
def robots_meta(context: Context, value: str) -> SafeString:
    context.render_context[_FLAG] = True
    content = NOINDEX if is_noindex(context.get("request")) else value
    return format_html('<meta name="robots" content="{}">', content)


@register.simple_tag(takes_context=True)
def default_robots_meta(context: Context) -> SafeString | str:
    if is_noindex(context.get("request")) and not context.render_context.get(_FLAG):
        return format_html('<meta name="robots" content="{}">', NOINDEX)
    return ""
