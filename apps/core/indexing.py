"""The environment floor for search engine visibility (SEO.8, D-35).

`MPMV_FORCE_NOINDEX` (true on STAGE) and demo mode (ARC.22) make every page noindex whatever a
page or, later, the SEO settings panel says. Kept in core so the catalog listings, the template
tag and the middleware read one rule.
"""

from django.conf import settings

NOINDEX = "noindex, nofollow"


def is_noindex_forced() -> bool:
    return bool(getattr(settings, "FORCE_NOINDEX", False) or getattr(settings, "DEMO_MODE", False))


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


def robots_txt(*, forced: bool) -> str:
    if forced:
        return "User-agent: *\nDisallow: /\n"
    lines = ["User-agent: *", *(f"Disallow: {path}" for path in PRIVATE_PATHS)]
    return "\n".join(lines) + "\n"
