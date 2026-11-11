"""Template tags for the public pages (CMS.1): per-language navigation and language switcher;
the page-level FAQPage structured data (CMS.2); the page's image credits (CMS.8)."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

from django import template
from django.conf import settings
from django.http import HttpRequest
from django.urls import reverse
from django.utils import translation
from django.utils.html import format_html_join
from django.utils.safestring import SafeString, mark_safe
from wagtail.models import Locale, Page, Site

from apps.core.i18n import (
    LanguageLink,
    configured_languages,
    hreflang_alternates,
    switch_goes_home,
    switcher_links,
    translate_path,
)
from apps.core.image_licences import Credit
from apps.core.services import NoPlatformOperator, current_operator

from .. import services
from ..engine import faq_page_jsonld, jsonld_text, organization_jsonld
from ..models import NavigationMenu

register = template.Library()


@dataclass(frozen=True)
class MenuLink:
    label: str
    url: str


def _menu(location: str) -> NavigationMenu | None:
    """The active language's menu, or the primary language's when it has none (spec 09)."""
    menus = NavigationMenu.objects.filter(location=location)
    menu: NavigationMenu | None = (
        menus.filter(locale=Locale.get_active()).first()
        or menus.filter(locale__language_code=settings.LANGUAGE_CODE).first()
    )
    return menu


@register.simple_tag(takes_context=True)
def navigation_links(context: dict[str, Any], location: str) -> list[MenuLink]:
    """The links of the header or footer menu; pages are shown in the active language.

    Links to deleted or unpublished pages are left out.
    """
    request: HttpRequest | None = context.get("request")
    menu = _menu(location)
    if menu is None:
        return []
    links: list[MenuLink] = []
    for block in menu.links:
        value = block.value
        if block.block_type == "external":
            links.append(MenuLink(label=value["label"], url=value["url"]))
            continue
        page = value["page"]
        if page is None:
            continue
        page = page.localized.specific
        if page.live:
            links.append(MenuLink(label=value["label"] or page.title, url=page.get_url(request)))
    return links


def _home(code: str, suffix: str = "") -> str:
    with translation.override(code):
        return reverse("home") + suffix


@register.simple_tag(takes_context=True)
def language_links(context: dict[str, Any]) -> list[LanguageLink]:
    """One entry per configured language: where the switcher takes the visitor.

    The target is the same object in that language: the translations a view puts in the context
    as `language_urls` (listings and storefronts, the set of their `hreflang` links), else the
    CMS page's live translations (slugs differ between languages). An object without a
    translation leads to the language's home, flagged so the switch shows a notice. A view with
    no object (search, cart, account, 404) keeps its own path in the other language, no notice.
    """
    request: HttpRequest | None = context.get("request")
    current = translation.get_language()
    query = request.META.get("QUERY_STRING", "") if request is not None else ""
    suffix = f"?{query}" if query else ""
    urls: Mapping[str, str] | None = context.get("language_urls")
    if urls is not None:  # a listing's filters mean nothing on a home page
        return switcher_links(urls, current, _home)
    page: Page | None = context.get("page")
    if page is not None:
        found = services.page_language_urls(page, request)
        return switcher_links(
            {code: url + suffix for code, url in found.items()},
            current,
            lambda code: _home(code, suffix),
        )
    if request is None or switch_goes_home(request):
        return switcher_links(
            {code: _home(code) for code in configured_languages()}, current, _home
        )
    return switcher_links(
        {code: translate_path(request.path_info, code) + suffix for code in configured_languages()},
        current,
        _home,
    )


@register.simple_tag(takes_context=True)
def switch_next_url(context: dict[str, Any]) -> str:
    """The `next` of the account and Manager switchers: the page's own path, except for a
    secret-bearing URL (`language_switch_home`), which must not reach the HTML."""
    request: HttpRequest | None = context.get("request")
    if request is None or switch_goes_home(request):
        return "/"
    return request.get_full_path()


@register.simple_tag(takes_context=True)
def page_hreflang(context: dict[str, Any]) -> SafeString:
    """The `<link rel="alternate" hreflang>` set of the page in the context, from the same
    translations map as its language switcher (`services.page_language_urls`)."""
    request: HttpRequest | None = context.get("request")
    page: Page | None = context.get("page")
    if request is None or page is None:
        return mark_safe("")
    urls = {
        code: request.build_absolute_uri(url)
        for code, url in services.page_language_urls(page, request).items()
    }
    return format_html_join(
        "\n  ", '<link rel="alternate" hreflang="{}" href="{}">', hreflang_alternates(urls)
    )


@register.simple_tag
def page_image_credits(page: Page) -> list[Credit]:
    """The credits for the page's "Image credits" disclosure (CMS.8)."""
    return services.page_image_credits(page)


@register.simple_tag
def faq_jsonld(page: Any) -> SafeString:
    """One FAQPage `<script>` for every question of the page's FAQ blocks, in order; "" if none.

    One page-level object, as search engines expect one FAQPage per page (spec 10).
    """
    questions = [
        (item["question"], str(item["answer"]))
        for block in getattr(page, "body", None) or []
        if block.block_type == "faq"
        for item in block.value["items"]
    ]
    if not questions:
        return mark_safe("")
    # JSON with <, > and & escaped: it cannot leave its script element (`jsonld_text`).
    data = jsonld_text(faq_page_jsonld(questions))
    return mark_safe(f'<script type="application/ld+json">{data}</script>')  # noqa: S308


@register.simple_tag
def jsonld_script(data: Mapping[str, Any] | None) -> SafeString:
    """`data` as a `<script type="application/ld+json">` (CMS.3 articles and author pages);
    "" for none. Escaped by `jsonld_text`, so it cannot leave its script element."""
    if not data:
        return mark_safe("")
    return mark_safe(f'<script type="application/ld+json">{jsonld_text(data)}</script>')  # noqa: S308


@register.simple_tag(takes_context=True)
def operator_organization_jsonld(context: template.Context) -> SafeString:
    """The platform operator as one Organization JSON-LD script (home page); "" without a
    request or a configured operator."""
    try:
        operator = current_operator()
    except NoPlatformOperator:
        return mark_safe("")
    # Absolute URLs come from the default site, never from the request's Host header (the page
    # may be served from a cache): the home page of the page's language, and the logo file.
    site = Site.objects.filter(is_default_site=True).first()
    if site is None:
        return mark_safe("")
    page: Page | None = context.get("page")
    home = (page.relative_url(site) if page is not None else None) or "/"
    logo_url = ""
    logo = operator.logo
    if logo is not None and logo.file:
        try:
            logo_url = urljoin(site.root_url, logo.file.url)
        except ValueError:  # no file behind the field
            logo_url = ""
    return jsonld_script(
        organization_jsonld(
            operator.legal_name,
            urljoin(site.root_url, home),
            operator.support_email,
            logo_url,
            operator.official_profiles,
        )
    )
