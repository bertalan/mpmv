"""Template tags for the public pages (CMS.1): per-language navigation and language switcher;
the page-level FAQPage structured data (CMS.2); the page's image credits (CMS.8)."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from django import template
from django.conf import settings
from django.core.cache import cache
from django.http import HttpRequest
from django.urls import NoReverseMatch, reverse
from django.utils import translation
from django.utils.translation import gettext
from wagtail.models import Locale, Page, Site

from apps.core.i18n import (
    LanguageLink,
    configured_languages,
    primary_language,
    switch_goes_home,
    switcher_links,
    translate_path,
)
from apps.core.image_licences import Credit

from .. import engine, services
from ..footer_cache import FOOTER_CACHE_SECONDS, footer_cache_key
from ..models import FooterSettings, NavigationMenu

register = template.Library()


@dataclass(frozen=True)
class MenuLink:
    label: str
    url: str
    network: str = ""  # social profiles only: the closed list of CMS.17, for the icon


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

    Links to deleted or unpublished pages are left out. The header menu ends with the
    temporary "Site links" item (OPS.12; remove before LIVE), for staff only (THM.21).
    """
    links = _menu_links(context.get("request"), _menu(location))
    user = getattr(context.get("request"), "user", None)
    staff = getattr(user, "is_active", False) and getattr(user, "is_staff", False)
    if location == NavigationMenu.Location.HEADER and staff:
        links.extend(_site_links_item())
    return links


def _site_links_item() -> list[MenuLink]:
    """The temporary staff "Site links" page (OPS.12); the caller adds it for staff only, so the
    menu of a visitor or a customer never shows a link they cannot open; remove before LIVE."""
    try:
        return [MenuLink(label=gettext("Site links"), url=reverse("site_links"))]
    except NoReverseMatch:  # a test URLconf without the route
        return []


def _menu_links(request: HttpRequest | None, menu: NavigationMenu | None) -> list[MenuLink]:
    return _stream_links(request, menu.links) if menu is not None else []


def _stream_links(request: HttpRequest | None, stream: Any) -> list[MenuLink]:
    links: list[MenuLink] = []
    for block in stream:
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


@dataclass(frozen=True)
class FooterColumnView:
    title: str
    links: list[MenuLink]


@register.simple_tag(takes_context=True)
def footer_chrome(context: dict[str, Any]) -> dict[str, Any]:
    """The footer's CMS content for the visitor's language (CMS.17), the same for every theme:
    `widgets` (up to four columns with `title`, `links`) and `social_links` (`label`,
    `url`). Empty lists when the site has no footer setting; a column with no title and no
    links is left out, so nothing renders empty."""
    request: HttpRequest | None = context.get("request")
    site = Site.find_for_request(request) if request is not None else None
    if site is None:
        return {"widgets": [], "social_links": []}
    language = translation.get_language() or settings.LANGUAGE_CODE
    key = footer_cache_key(site.pk, language)
    cached: dict[str, Any] | None = cache.get(key)
    if cached is None:
        cached = _footer_content(request, site, language)
        cache.set(key, cached, FOOTER_CACHE_SECONDS)
    return cached


FOOTER_VENDOR_LOGOS = 12


@register.simple_tag
def footer_vendor_logos() -> list[dict[str, Any]]:
    """The strip above the `ride` footer (THM.22): active vendors with a logo, by display name,
    at most 12, two queries (the vendors with their logos, then the renditions).
    The alt text is the vendor's display name; the logo is not a link."""
    from apps.core.images import prefetch_renditions
    from apps.vendors.services import storefront_vendors

    vendors = (
        storefront_vendors()
        .filter(logo__isnull=False)
        .select_related("logo")
        .order_by("display_name", "id")[:FOOTER_VENDOR_LOGOS]
    )
    found = list(vendors)
    prefetch_renditions(vendor.logo for vendor in found)  # one query, not one per logo
    return [{"name": vendor.display_name, "image": vendor.logo} for vendor in found]


def _footer_content(request: HttpRequest | None, site: Site, language: str) -> dict[str, Any]:
    footer = FooterSettings.objects.filter(site=site).first()
    if footer is None:
        return {"widgets": [], "social_links": []}
    widgets = []
    for column in engine.footer_columns(((c.language, c) for c in footer.columns.all()), language):
        view = FooterColumnView(title=column.title, links=_stream_links(request, column.links))
        if view.title or view.links:
            widgets.append(view)
    profiles = engine.footer_profiles(((p.language, p) for p in footer.profiles.all()), language)
    return {
        "widgets": widgets,
        "social_links": [MenuLink(label=p.label, url=p.url, network=p.network) for p in profiles],
    }


def _home(code: str, suffix: str = "") -> str:
    with translation.override(code):
        return reverse("home") + suffix


def _stay(
    urls: Mapping[str, str], current: str, *, fallback: bool
) -> Callable[[str], tuple[str, bool] | None]:
    """Where a switch to a language without a translation stays (`switcher_links`): the
    primary-language page under the new prefix when the object falls back to it, else the page
    the visitor is on (the switch flashes the notice); None, hence the home, with no page at all."""
    primary = primary_language()

    def stay(code: str) -> tuple[str, bool] | None:
        if fallback and primary in urls:
            return translate_path(urls[primary], code), False
        own = urls.get(current) or next(iter(urls.values()), None)
        return (own, True) if own is not None else None

    return stay


@register.simple_tag(takes_context=True)
def language_links(context: dict[str, Any]) -> list[LanguageLink]:
    """One entry per configured language: where the switcher takes the visitor.

    The target is the same object in that language: the translations a view puts in the context
    as `language_urls` (listings and storefronts, the set of their `hreflang` links), else the
    CMS page's live translations (slugs differ between languages). An object without a
    translation stays on the page (I18N.22, owner decision 2026-11-21): the same object under
    the new prefix, where the primary-language fallback (I18N.21, catalog objects) shows it with
    its own notice, or, when nothing falls back (an event, an object with no primary-language
    text), the page the visitor is on, with the switch flashing the notice. A view with no object
    (search, cart, account, 404) keeps its own path in the other language, no notice; a page
    marked `language_switch_home` (secret URL) goes to the target home, no notice.
    """
    request: HttpRequest | None = context.get("request")
    current = translation.get_language()
    query = request.META.get("QUERY_STRING", "") if request is not None else ""
    suffix = f"?{query}" if query else ""
    urls: Mapping[str, str] | None = context.get("language_urls")
    if urls is not None:  # the catalog's objects always fall back to the primary language
        return switcher_links(urls, current, _home, _stay(urls, current, fallback=True))
    page: Page | None = context.get("page")
    if page is not None:
        found = services.page_language_urls(page, request)
        return switcher_links(
            {code: url + suffix for code, url in found.items()},
            current,
            lambda code: _home(code, suffix),
            _stay(
                {code: url + suffix for code, url in found.items()},
                current,
                fallback=bool(getattr(page.specific_class, "language_fallback", True)),
            ),
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


@register.simple_tag
def page_image_credits(page: Page) -> list[Credit]:
    """The credits for the page's "Image credits" disclosure (CMS.8)."""
    return services.page_image_credits(page)


@register.simple_tag(takes_context=True)
def page_trail(
    context: dict[str, Any], page: Page | None = None, current: str = ""
) -> list[dict[str, str]]:
    """The breadcrumb of a content page (THM.9): Home, the page's live public ancestors below
    its language's home, then the page itself, last, as the current page.

    `current` names a deeper current page that is not in the tree (an author, an archived
    version, a system page): the page then becomes a link before it. Without `page` the trail is
    Home and `current`. The labels match the `BreadcrumbList` of the page graph (`seo`).
    """
    request: HttpRequest | None = context.get("request")
    try:
        home = reverse("home")
    except NoReverseMatch:  # a URLconf without the public routes
        home = "/"
    trail = [{"label": str(gettext("Home")), "url": home}]
    if page is not None:
        for ancestor in page.get_ancestors().filter(depth__gte=3).live().public():
            trail.append({"label": str(ancestor.title), "url": ancestor.get_url(request) or ""})
        trail.append({"label": str(page.title), "url": page.get_url(request) or ""})
    if current:
        trail.append({"label": current, "url": ""})
    return trail


# --- Contextual help (ARC.32)


@register.simple_tag(takes_context=True)
def help_context(context: dict[str, Any]) -> dict[str, str] | None:
    """What the help button needs, for a signed-in user only; None for anyone else.

    The route name and the page type say which entry the dialog asks for. The user is not looked
    up without a session cookie: that would read the session and add `Vary: Cookie` to every
    public page, and a visitor has no help button anyway (the signed-in chrome is never cached).
    """
    request: HttpRequest | None = context.get("request")
    if request is None or settings.SESSION_COOKIE_NAME not in request.COOKIES:
        return None
    user = getattr(request, "user", None)
    if user is None or not (user.is_authenticated and user.is_active):
        return None
    try:
        url = reverse("help_entry")
    except NoReverseMatch:  # a URLconf without the endpoint (a test harness): no button
        return None
    match = getattr(request, "resolver_match", None)
    page = context.get("page")
    label = page.specific_class._meta.label if isinstance(page, Page) else ""
    return {
        "url": url,
        "route": engine.help_route_name(getattr(match, "view_name", "")),
        "page_type": engine.help_page_type(label),
        "language": translation.get_language() or settings.LANGUAGE_CODE,
    }


@register.simple_tag
def legal_anchors(body: Any) -> dict[str, str]:
    """Anchor id by block id for the legal sections of a body (THM.29)."""
    sections = [
        (str(c.value["section"]), str(c.id)) for c in body if c.block_type == "legal_section"
    ]
    anchors = engine.legal_section_anchors(sections)
    return {block_id: anchor for (_, block_id), anchor in zip(sections, anchors, strict=True)}


@register.filter
def anchor_of(anchors: dict[str, str], block_id: Any) -> str:
    """The anchor of one block ('' when it has none)."""
    return anchors.get(str(block_id), "")


@register.simple_tag(takes_context=True)
def language_fallback_notice(context: dict[str, Any]) -> str:
    """The language of the text shown in place of a missing translation (I18N.21), or "".

    The notice itself is rendered by the layout, in the visitor's language, with the endonym of
    this one (`language_fallback_endonym`)."""
    request: HttpRequest | None = context.get("request")
    return str(getattr(request, "language_fallback", "") or "")


@register.simple_tag
def language_fallback_endonym(code: str) -> str:
    """The language's own name ("Italiano"), for the fallback notice."""
    return str(translation.get_language_info(code)["name_local"])
