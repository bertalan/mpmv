"""Template tags for the public pages (CMS.1): per-language navigation and language switcher;
the page-level FAQPage structured data (CMS.2); the page's image credits (CMS.8)."""

from dataclasses import dataclass
from typing import Any

from django import template
from django.conf import settings
from django.http import HttpRequest
from django.urls import reverse
from django.utils import translation
from django.utils.safestring import SafeString, mark_safe
from wagtail.models import Locale, Page

from apps.core.i18n import configured_languages
from apps.core.image_licences import Credit

from .. import services
from ..engine import faq_page_jsonld, jsonld_text
from ..models import NavigationMenu

register = template.Library()


@dataclass(frozen=True)
class MenuLink:
    label: str
    url: str


@dataclass(frozen=True)
class LanguageLink:
    code: str
    name: str  # in its own language ("Italiano")
    url: str  # the current page in that language, query string kept
    current: bool


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


def _translations(page: Page | None, request: HttpRequest | None) -> dict[str, str]:
    """{language code: URL} of the live translations of `page` (itself included)."""
    if page is None:
        return {}
    return {
        translated.locale.language_code: translated.get_url(request)
        for translated in page.get_translations(inclusive=True).live().select_related("locale")
    }


@register.simple_tag(takes_context=True)
def language_links(context: dict[str, Any]) -> list[LanguageLink]:
    """One entry per configured language: where the switcher takes the visitor.

    On a CMS page, the target is that page's translation (slugs differ between languages);
    without one, the language's home. Elsewhere, the language's home.
    """
    request: HttpRequest | None = context.get("request")
    page: Page | None = context.get("page")
    query = request.META.get("QUERY_STRING", "") if request is not None else ""
    suffix = f"?{query}" if query else ""
    current = translation.get_language()
    infos = {code: translation.get_language_info(code) for code in configured_languages()}
    translated = _translations(page, request)
    links: list[LanguageLink] = []
    for code, info in infos.items():
        url = translated.get(code)
        if url is None:
            with translation.override(code):
                url = reverse("home")
        links.append(
            LanguageLink(
                code=code, name=str(info["name_local"]), url=url + suffix, current=code == current
            )
        )
    return links


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
