"""Template tags for the public pages (CMS.1): per-language navigation and language switcher;
the page-level FAQPage structured data (CMS.2); the page's image credits (CMS.8)."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from django import template
from django.conf import settings
from django.http import HttpRequest
from django.urls import reverse
from django.utils import translation
from django.utils.safestring import SafeString, mark_safe
from wagtail.models import Locale, Page

from apps.core.i18n import LanguageLink, switcher_links
from apps.core.image_licences import Credit

from .. import services
from ..engine import faq_page_jsonld, jsonld_text
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


@register.simple_tag(takes_context=True)
def language_links(context: dict[str, Any]) -> list[LanguageLink]:
    """One entry per configured language: where the switcher takes the visitor.

    The target is the same object in that language: the translations a view puts in the context
    as `language_urls` (listings and storefronts, the set of their `hreflang` links), else the
    CMS page's live translations (slugs differ between languages). Without a translation, the
    language's home, flagged so the switch shows a notice.
    """
    request: HttpRequest | None = context.get("request")
    query = request.META.get("QUERY_STRING", "") if request is not None else ""
    suffix = f"?{query}" if query else ""
    urls: Mapping[str, str] | None = context.get("language_urls")
    if urls is None:
        urls = {
            code: url + suffix
            for code, url in services.page_language_urls(context.get("page"), request).items()
        }

    if "language_urls" in context:
        suffix = ""  # a listing's filters mean nothing on a home page

    def home(code: str) -> str:
        with translation.override(code):
            return reverse("home") + suffix

    return switcher_links(urls, translation.get_language(), home)


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
