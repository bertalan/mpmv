"""Temporary staff "Site links" page (OPS.12, owner request 2026-11-12; remove before LIVE).

Every page a person can open in a browser, built at request time from the URL configuration
and the data, so the list never goes stale: the live public CMS pages of each language, the
active vendor storefronts, the catalogue listings, and the fixed account, Manager, back office
and system pages that `reverse()` still finds. Left out on purpose: routes that need a
parameter no data fills, POST-only views, JSON and option endpoints, signed token links,
webhooks, media and static files.

`apps.seo.site_links_engine` shapes the rows into capped sections; the view stays thin.
"""

from collections.abc import Callable, Iterable
from typing import Any

from django.conf import settings
from django.http import HttpRequest
from django.urls import NoReverseMatch, Resolver404, resolve, reverse
from django.utils import translation
from django.utils.translation import gettext as _
from wagtail.models import Locale, Page

from apps.catalog import services as catalog
from apps.catalog import slugs
from apps.catalog.models import Brand, Category, Taxonomy, Term
from apps.core.services import NoPlatformOperator, current_operator
from apps.vendors.models import Vendor

from .site_links_engine import LIMIT, Link, Section, routed_links, section, tree_links

PAGE_LIMIT = 200  # CMS pages per language: the tree is the main thing to test

# A CMS page path that Wagtail's catch-all serves; anything else has its own route.
_CMS_CATCH_ALL = "wagtail_serve"


def _reverse(name: str) -> str | None:
    try:
        return reverse(name)
    except NoReverseMatch:
        return None


def _account_routes() -> list[tuple[str, str]]:
    return [
        (_("Sign in"), "login"),
        (_("Two-factor authentication"), "mfa:overview"),
        (_("Set up two-factor authentication"), "mfa:setup"),
        (_("Privacy and your data"), "privacy_account"),
        (_("Erase your account (asks for confirmation)"), "privacy_erase"),
        (_("Privacy requests (staff)"), "privacy_requests"),
    ]


def _manager_routes() -> list[tuple[str, str]]:
    return [
        (_("Shop space: dashboard"), "manager:shop:dashboard"),
        (_("Shop space: storefront profile"), "manager:shop:profile"),
        (_("Shop space: orders"), "manager:shop:orders"),
        (_("Shop space: choose the shop"), "manager:shop:vendor"),
        (_("Platform space: dashboard"), "manager:platform:dashboard"),
        (_("Platform space: content reports"), "manager:platform:notices"),
    ]


def _back_office_routes() -> list[tuple[str, str]]:
    return [
        (_("Editors' admin (Wagtail)"), "wagtailadmin_home"),
        (_("Technical admin (Django)"), "admin:index"),
    ]


def _has_contact_points() -> bool:
    """Whether the DSA contact page has something to show (it answers 404 otherwise)."""
    try:
        operator = current_operator()
    except NoPlatformOperator:
        return False
    return bool(
        operator.authority_contact_email or operator.support_email or operator.user_contact_url
    )


def _system_routes() -> list[tuple[str, str]]:
    contact = [(_("DSA points of contact"), "dsa_contact_points")] if _has_contact_points() else []
    return [
        (_("API documentation"), "api-v1:openapi-view"),
        (_("Styleguide"), "themes:styleguide"),
        (_("Liveness probe"), "healthz"),
        (_("Readiness probe"), "readyz"),
        (_("robots.txt"), "robots_txt"),
        (_("Privacy sub-processors"), "subprocessors"),
        *contact,
        (_("Report illegal content (DSA)"), "dsa_notice"),
        (_("Sell with us (vendor application)"), "vendors:apply"),
    ]


def _cms_sections(request: HttpRequest) -> list[Section]:
    names = dict(settings.LANGUAGES)
    sections = []
    for locale in Locale.objects.order_by("language_code"):
        pages = Page.objects.live().public().filter(locale=locale, depth__gte=2).order_by("path")
        total = pages.count()
        if not total:
            continue
        rows = ((page.title, page.get_url(request), page.depth) for page in pages[:PAGE_LIMIT])
        title = _("Storefront pages: %(language)s") % {
            "language": names.get(locale.language_code, locale.language_code)
        }
        sections.append(
            section(title, tree_links(rows, base_depth=2), limit=PAGE_LIMIT, total=total)
        )
    return sections


def _shops(language: str) -> Section:
    segment = slugs.segment_for(slugs.SHOP_SEGMENTS, language)
    vendors = Vendor.objects.filter(status=Vendor.Status.ACTIVE).order_by("display_name", "id")
    links = [
        Link(
            label=str(vendor.display_name),
            path=reverse("catalog:vendor", kwargs={"segment": segment, "slug": vendor.slug}),
        )
        for vendor in vendors[:LIMIT]
    ]
    return section(_("Shops"), links, total=vendors.count())


def _anchored(
    rows: Iterable[Any], scope: Callable[[Any], catalog.ListingScope], language: str
) -> list[Link]:
    """The listing page of each row in `language`; rows without one are left out."""
    links = []
    for row in rows:
        anchor = catalog.scope_anchor(scope(row), language)
        path = anchor.paths.get(language) if anchor is not None else None
        if anchor is not None and path:
            depth = int(getattr(row, "depth", 1)) - 1
            links.append(Link(label=anchor.name or path, path=path, depth=depth))
    return links


def _taxonomy_name(taxonomy: Taxonomy, language: str) -> str:
    text = taxonomy.translation(language).translation
    return str(text.name) if text is not None and text.name else str(taxonomy.code)


def _catalogue_sections(language: str) -> list[Section]:
    sections = []
    for taxonomy in Taxonomy.objects.filter(has_landing=True).order_by("code"):
        terms = Term.objects.filter(taxonomy=taxonomy).order_by("path")
        links = _anchored(terms[:LIMIT], lambda term: catalog.ListingScope(term=term), language)
        title = _("Catalogue: %(taxonomy)s") % {"taxonomy": _taxonomy_name(taxonomy, language)}
        sections.append(section(title, links, total=terms.count()))
    categories = Category.objects.order_by("path")
    sections.append(
        section(
            _("Catalogue: categories"),
            _anchored(
                categories[:LIMIT], lambda item: catalog.ListingScope(category=item), language
            ),
            total=categories.count(),
        )
    )
    brands = Brand.objects.filter(status=Brand.Status.APPROVED).order_by("name", "id")
    sections.append(
        section(
            _("Catalogue: brands"),
            _anchored(brands[:LIMIT], lambda brand: catalog.ListingScope(brand=brand), language),
            total=brands.count(),
        )
    )
    sections.append(_products(language))
    return sections


def _has_own_route(path: str) -> bool:
    """Whether `path` is served by a route of its own, not by the CMS page lookup."""
    try:
        return resolve(path).url_name != _CMS_CATCH_ALL
    except Resolver404:
        return False


def _products(language: str) -> Section:
    title = _("Catalogue: products")
    products = catalog.listed_products().order_by("-id")
    cards = [card for card in catalog.product_cards(list(products[:LIMIT]), language) if card.url]
    if cards and not _has_own_route(cards[0].url or ""):
        note = _("Product pages are not built yet (CAT.7): products are listed once they are.")
        return section(title, [], note=note)
    links = [Link(label=card.title, path=card.url or "") for card in cards]
    return section(title, links, total=products.count())


def site_link_sections(request: HttpRequest) -> list[Section]:
    """Every section of the page, in the language of the request."""
    language = translation.get_language() or settings.LANGUAGE_CODE
    return [
        *_cms_sections(request),
        _shops(language),
        *_catalogue_sections(language),
        section(_("Account"), routed_links(_account_routes(), _reverse)),
        section(_("Manager"), routed_links(_manager_routes(), _reverse)),
        section(
            _("Back office"),
            routed_links(_back_office_routes(), _reverse),
            note=_("On STAGE and LIVE these may answer only from the server's own network."),
        ),
        section(_("Developer and system"), routed_links(_system_routes(), _reverse)),
    ]
