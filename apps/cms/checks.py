"""System checks of the CMS (CMS.1), registered in `CmsConfig.ready`.

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


def check_no_page_restrictions(
    app_configs: Iterable[AppConfig] | None,
    databases: Iterable[str] | None = None,
    **kwargs: Any,
) -> list[checks.CheckMessage]:
    """cms.E002: no page view restriction (password or login) may exist while the routes of their
    forms are blocked (BLOCK_PAGE_RESTRICTION_ROUTES, SEC.13/SEC.14): the page could never be
    opened, and the form it needs is not protected yet. Runs only when a database is checked."""
    from django.conf import settings
    from django.db import DatabaseError
    from wagtail.models import PageViewRestriction

    if not databases or not settings.BLOCK_PAGE_RESTRICTION_ROUTES:
        return []
    messages: list[checks.CheckMessage] = []
    for alias in databases:
        try:
            count = (
                PageViewRestriction.objects.using(alias)
                .exclude(restriction_type=PageViewRestriction.GROUPS)
                .count()
            )
        except DatabaseError:
            continue  # not migrated yet
        if count:
            messages.append(
                checks.Error(
                    f"{count} page view restriction(s) use a password or login while their form "
                    "routes are blocked (SEC.14 has not protected them yet).",
                    hint="Remove the restriction in the editors' admin (page privacy).",
                    id="cms.E002",
                )
            )
    return messages
