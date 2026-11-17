"""The public root URL comes from `MPMV_BASE_URL` (I18N.18).

`settings.WAGTAILADMIN_BASE_URL` (fed by `MPMV_BASE_URL`) is the one source of the scheme, host
and port of absolute URLs: sitemaps, `llms.txt`, robots.txt. `sync_default_site()` (run by the
`seed`) copies its host and port to the default Wagtail Site, so canonical and `hreflang` links,
which Wagtail builds from the Site row, agree with it.
"""

from urllib.parse import urlsplit

from django.conf import settings
from wagtail.models import Site

from .site_checks import hostname_problem


def base_url_parts() -> tuple[str, str, int] | None:
    """(scheme, hostname, port) of the base URL; None when it names no real host."""
    parts = urlsplit(str(getattr(settings, "WAGTAILADMIN_BASE_URL", "") or ""))
    if not parts.hostname or hostname_problem(parts.hostname) is not None:
        return None
    return parts.scheme, parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)


def sync_default_site(*, dry_run: bool = False) -> list[str]:
    """Make the default Site's hostname and port match the base URL; a line per change."""
    root = base_url_parts()
    site = Site.objects.filter(is_default_site=True).first()
    if root is None or site is None or (site.hostname, site.port) == root[1:]:
        return []
    line = f"{site.hostname}:{site.port} -> {root[1]}:{root[2]}"
    if not dry_run:
        site.hostname, site.port = root[1], root[2]
        site.save(update_fields=["hostname", "port"])
    return [line]
