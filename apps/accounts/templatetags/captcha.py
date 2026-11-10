"""Template tags for the Turnstile widget (CORE.5). Both render nothing when it is disabled.

{% load captcha %}
{% captcha_script %}   in <head>, with the page's CSP nonce
{% captcha_widget %}   inside the <form>, so the token is submitted with it
"""

from django import template
from django.conf import settings
from django.utils.html import format_html
from django.utils.safestring import SafeString

from apps.accounts.captcha import TURNSTILE_SCRIPT_URL

register = template.Library()


def _site_key() -> str:
    return str(settings.TURNSTILE_SITE_KEY) if settings.CAPTCHA_ENABLED else ""


@register.simple_tag(takes_context=True)
def captcha_script(context: template.Context) -> SafeString:
    if not _site_key():
        return SafeString("")
    nonce = context.get("csp_nonce")
    if nonce:
        return format_html(
            '<script src="{}" async defer nonce="{}"></script>', TURNSTILE_SCRIPT_URL, nonce
        )
    return format_html('<script src="{}" async defer></script>', TURNSTILE_SCRIPT_URL)


@register.simple_tag
def captcha_widget() -> SafeString:
    site_key = _site_key()
    if not site_key:
        return SafeString("")
    # No puzzle (CMP.9, WCAG 2.2 SC 3.3.8, spec 14 §14.6): the site key is a Managed or
    # Invisible widget, shown only when Cloudflare needs one click on its accessible checkbox,
    # and a failed check retries by itself instead of asking the user to solve anything.
    return format_html(
        '<div class="cf-turnstile" data-sitekey="{}" data-appearance="interaction-only"'
        ' data-retry="auto"></div>',
        site_key,
    )
