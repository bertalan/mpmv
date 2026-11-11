"""The environment floor for search engine visibility (SEO.8, D-35).

`MPMV_FORCE_NOINDEX` (true on STAGE) and demo mode (ARC.22) make every page noindex whatever a
page or, later, the SEO settings panel says. Kept in core so the catalog listings, the template
tag and the middleware read one rule.
"""

from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest

NOINDEX = "noindex, nofollow"

# Set by the SEO app (apps.seo, a higher layer): whether the site of a request has allowed
# indexing in its SEO settings. Without it (no SEO app) a site allows indexing.
SitePolicy = Callable[[HttpRequest | None], bool]
_site_policy: SitePolicy | None = None
_REQUEST_ATTR = "_mpmv_site_allows_indexing"


def is_noindex_forced() -> bool:
    """The environment floor: `MPMV_FORCE_NOINDEX` or demo mode. Nothing overrides it."""
    return bool(getattr(settings, "FORCE_NOINDEX", False) or getattr(settings, "DEMO_MODE", False))


def register_site_policy(policy: SitePolicy | None) -> None:
    global _site_policy
    _site_policy = policy


def site_allows_indexing(request: HttpRequest | None) -> bool:
    """The site's own switch (SEO.8 2/2), read once per request; it can only add noindex."""
    if _site_policy is None or request is None:  # no request: not a page of any site
        return True
    cached: bool | None = getattr(request, _REQUEST_ATTR, None)
    if cached is not None:
        return cached
    allowed = _site_policy(request)
    setattr(request, _REQUEST_ATTR, allowed)
    return allowed


def is_noindex(request: HttpRequest | None) -> bool:
    """Whether every page of the request's site is noindex: the floor, else the site's switch."""
    return is_noindex_forced() or not site_allows_indexing(request)


# Areas that never belong in a search index: Manager, accounts and MFA, staff admin, editors'
# admin (Wagtail), the language switcher, the API.
PRIVATE_PATHS = (
    "/manager/",
    "/account/",
    "/django-admin/",
    "/admin/",
    "/i18n/",
    "/api/",
)


def robots_txt(*, forced: bool, sitemap_url: str = "") -> str:
    """`forced` is "every page is noindex" (the floor or the site's switch)."""
    if forced:
        return "User-agent: *\nDisallow: /\n"
    lines = ["User-agent: *", *(f"Disallow: {path}" for path in PRIVATE_PATHS)]
    if sitemap_url:
        lines += ["", f"Sitemap: {sitemap_url}"]
    return "\n".join(lines) + "\n"
