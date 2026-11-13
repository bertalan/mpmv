"""Consent tags for the layouts and for pages that load a third-party script (SEO.5, SEO.9).

`{% consent_banner %}` prints the provider's banner (the same for every visitor, so a cached
page stays valid). `{% consent_settings_link %}` prints the "Cookie settings" link for the footer.
`{% consent_script "statistics" "https://…/tag.js" %}` prints a script that does not load until
the visitor grants that category. All three only call the active `ConsentProvider`, so the tag
code does not change when an operator selects another provider.
"""

from django import template
from django.core.exceptions import ImproperlyConfigured
from django.template.context import Context
from django.urls import NoReverseMatch
from django.utils.html import format_html
from django.utils.safestring import SafeString, mark_safe
from django.utils.translation import gettext

from apps.seo.providers import get_provider

register = template.Library()


@register.simple_tag(takes_context=True)
def consent_banner(context: Context) -> SafeString:
    try:
        provider = get_provider()
    except ImproperlyConfigured:  # reported by the seo.E001 check; a page still renders
        return mark_safe("")
    try:
        return mark_safe(provider.render_banner(context.get("request")))  # noqa: S308 (our template)
    except NoReverseMatch:  # a URLconf without the public routes (test URLconfs)
        return mark_safe("")


@register.simple_tag
def consent_settings_link() -> SafeString:
    try:
        url = get_provider().preferences_url()
    except (ImproperlyConfigured, NoReverseMatch):
        return mark_safe("")
    if not url:
        return mark_safe("")
    return format_html(
        '<a class="consent-settings-link" href="{}">{}</a>', url, gettext("Cookie settings")
    )


@register.simple_tag
def consent_script(category: str, src: str) -> SafeString:
    """A script held back until `category` is granted. Never prints a loadable `src`."""
    return mark_safe(get_provider().gate_script(category, src))  # noqa: S308 (provider markup)
