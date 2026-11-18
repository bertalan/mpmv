"""System checks of the CMS (CMS.1), registered in `CmsConfig.ready`.

- cms.E002: MPMV_VENDOR_GUIDE_URL is empty or an absolute https URL (ARC.32).
- cms.E004: no live root-level page uses a reserved slug (ARC.37).
- cms.E003: MPMV_VIDEO_EMBED_ORIGINS holds plain https origins only (THM.14).
- cms.E001 (deploy): WAGTAILADMIN_BASE_URL is the public address of the site. Wagtail puts it in
  the links of its admin emails and previews; the settings fall back to localhost when
  MPMV_BASE_URL is unset, which is right on a developer machine only.
"""

from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

from django.apps import AppConfig
from django.conf import settings
from django.core import checks

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _is_public(url: str) -> bool:
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(host) and host not in LOOPBACK_HOSTS


def check_base_url(
    app_configs: Iterable[AppConfig] | None, **kwargs: Any
) -> list[checks.CheckMessage]:
    url = getattr(settings, "WAGTAILADMIN_BASE_URL", None) or ""
    if _is_public(url):
        return []
    return [
        checks.Error(
            f"WAGTAILADMIN_BASE_URL is {url!r}, not the public address of the site.",
            hint="Set MPMV_BASE_URL, e.g. https://shop.example.com (no trailing slash).",
            id="cms.E001",
        )
    ]


def check_vendor_guide_url(
    app_configs: Iterable[AppConfig] | None, **kwargs: Any
) -> list[checks.CheckMessage]:
    """cms.E002: MPMV_VENDOR_GUIDE_URL (ARC.32) is empty or an absolute https URL. The help
    dialog links it from every vendor-facing screen, so no other scheme is accepted."""
    from . import engine

    url = getattr(settings, "MPMV_VENDOR_GUIDE_URL", "")
    if engine.help_guide_url_valid(url):
        return []
    return [
        checks.Error(
            "MPMV_VENDOR_GUIDE_URL is not an absolute https URL.",
            hint="Set it to https://guide.example.com/vendors/ or leave it empty.",
            id="cms.E002",
        )
    ]


def check_video_embed_origins(
    app_configs: Iterable[AppConfig] | None, **kwargs: Any
) -> list[checks.CheckMessage]:
    """cms.E003: every MPMV_VIDEO_EMBED_ORIGINS entry is a plain `https://host` origin. The list
    goes into the CSP `frame-src`, so a `*`, a wildcard host, `http://` or a path is refused."""
    from . import engine

    errors: list[checks.CheckMessage] = []
    for origin in getattr(settings, "MPMV_VIDEO_EMBED_ORIGINS", ()):
        problem = engine.video_origin_problem(origin)
        if problem:
            errors.append(
                checks.Error(
                    f"MPMV_VIDEO_EMBED_ORIGINS entry {origin!r} is refused: {problem}.",
                    hint="List plain https origins, e.g. https://player.vimeo.com, "
                    "separated by commas.",
                    id="cms.E003",
                )
            )
    return errors


def check_reserved_root_slugs(
    app_configs: Iterable[AppConfig] | None, **kwargs: Any
) -> list[checks.CheckMessage]:
    """cms.E004: no page directly under a language home has a reserved slug (`help-centre`),
    as an app route is matched first and the page would never be reached. Skipped while the
    database is unavailable or not migrated."""
    from django.db import DatabaseError
    from wagtail.models import Page

    from . import engine

    try:
        clashing = list(
            Page.objects.filter(depth=3, slug__in=engine.RESERVED_ROOT_SLUGS).values_list(
                "slug", "locale__language_code"
            )
        )
    except DatabaseError:
        return []
    return [
        checks.Error(
            f"A page with the reserved slug {slug!r} ({language}) sits directly under a home "
            "page, so a route shadows it.",
            hint="Rename the page: its URL is used by the help pages.",
            id="cms.E004",
        )
        for slug, language in clashing
    ]
