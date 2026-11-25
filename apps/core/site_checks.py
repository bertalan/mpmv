"""System checks of the public root URL (I18N.18).

- core.E921: where live data is held (LIVE and STAGE, not DEBUG), `MPMV_BASE_URL` is not set to a
  real https host. Absolute URLs (sitemaps, `llms.txt`) are built on it.
- core.W921: the default Wagtail Site's hostname or port disagrees with the base URL (the `seed`
  syncs it), or the base URL is not real outside live data (development, tests).
"""

from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

from django.apps import AppConfig
from django.conf import settings
from django.core import checks
from django.db import connection
from wagtail.models import Site

from .migration_safety import migrations_pending

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


def base_url_problem() -> str | None:
    """Why `WAGTAILADMIN_BASE_URL` (from `MPMV_BASE_URL`) is not a real https root; else None."""
    url = str(getattr(settings, "WAGTAILADMIN_BASE_URL", "") or "")
    parts = urlsplit(url)
    if not parts.hostname or hostname_problem(parts.hostname) is not None:
        return f"MPMV_BASE_URL is not set to a real host ({url!r})"
    if parts.scheme != "https":
        return f"MPMV_BASE_URL is not https ({url!r})"
    return None


def check_site_hostname(
    app_configs: Iterable[AppConfig] | None, **kwargs: Any
) -> list[checks.CheckMessage]:
    out: list[checks.CheckMessage] = []
    problem = base_url_problem()
    hint = "Set MPMV_BASE_URL to the public https root, e.g. https://shop.example."
    if problem is not None:
        if site_is_strict() and not settings.DEBUG:
            return [checks.Error(problem + ".", hint=hint, id="core.E921")]
        out.append(checks.Warning(problem + ".", hint=hint, id="core.W921"))
    if Site._meta.db_table not in connection.introspection.table_names():
        return out  # `migrate` runs the database checks before it creates the table
    if migrations_pending("wagtailcore"):
        return out  # CORE.27: the Site table may lack columns a pending migration adds
    from .site_root import base_url_parts

    root = base_url_parts()
    site = Site.objects.filter(is_default_site=True).first()
    if root is not None and (site is None or (site.hostname, site.port) != root[1:]):
        found = "no default Site" if site is None else f"{site.hostname}:{site.port}"
        out.append(
            checks.Warning(
                f"The default Wagtail Site ({found}) differs from MPMV_BASE_URL "
                f"({root[1]}:{root[2]}): canonical links use the Site.",
                hint="Run `manage.py seed` (it syncs the Site) or edit Settings, Sites.",
                id="core.W921",
            )
        )
    return out
