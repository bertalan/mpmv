"""System check of the default Wagtail Site (I18N.18).

- core.E921: where live data is held (LIVE and STAGE), the default Site has no real hostname
  (empty, the installer placeholder `localhost`, a scheme or a path in it, or no default Site).
  Canonical, `hreflang` and sitemap URLs are built on it, so they would be wrong or relative.
- core.W921: the same finding elsewhere (development, tests): a warning.
"""

from collections.abc import Iterable
from typing import Any

from django.apps import AppConfig
from django.conf import settings
from django.core import checks
from django.db import connection
from wagtail.models import Site

PLACEHOLDER_HOSTNAME = "localhost"  # what the installer (cms migration 0002) creates


def hostname_problem(hostname: str) -> str | None:
    """Why `hostname` cannot be a public site hostname; None when it can."""
    if not hostname.strip():
        return "is empty"
    if hostname.strip().lower() == PLACEHOLDER_HOSTNAME:
        return "is the installer placeholder 'localhost'"
    if "://" in hostname or "/" in hostname or any(c.isspace() for c in hostname):
        return "must be a bare hostname (no scheme, path or space)"
    return None


def site_is_strict() -> bool:
    """Live data is held here: a wrong site root must stop the deploy and the request."""
    return bool(getattr(settings, "LIVE_DATA", False) or getattr(settings, "STAGE", False))


def check_site_hostname(
    app_configs: Iterable[AppConfig] | None, **kwargs: Any
) -> list[checks.CheckMessage]:
    if Site._meta.db_table not in connection.introspection.table_names():
        return []  # `migrate` runs the database checks before it creates the table
    site = Site.objects.filter(is_default_site=True).first()
    problem = "There is no default Site" if site is None else hostname_problem(site.hostname)
    if site is not None and problem is not None:
        problem = f"The default Site hostname {site.hostname!r} {problem}"
    if problem is None:
        return []
    hint = "Set the default Site's hostname (and port 443) in the Wagtail admin: Settings, Sites."
    if site_is_strict() and not settings.DEBUG:
        return [checks.Error(problem + ".", hint=hint, id="core.E921")]
    return [checks.Warning(problem + ".", hint=hint, id="core.W921")]
