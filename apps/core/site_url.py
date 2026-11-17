"""Absolute URLs on the configured base URL, never the Host header (I18N.6, I18N.18).

Sitemap, `llms.txt` and robots.txt URLs are public SEO signals: a forged `Host` must not be able
to put its own domain in them. They are built on `settings.WAGTAILADMIN_BASE_URL` (fed by
`MPMV_BASE_URL`: scheme, host and port). Where live data is held a base URL that is not a real
https root raises `ImproperlyConfigured` (the `core.E921` check says so at deploy time).
"""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest

from .site_checks import base_url_problem, site_is_strict


def site_root_url(request: HttpRequest | None = None) -> str:
    """The base URL without a trailing slash. Never empty."""
    problem = base_url_problem()
    if problem is not None and site_is_strict():
        raise ImproperlyConfigured(f"Absolute URLs need a real base URL: {problem}.")
    return str(settings.WAGTAILADMIN_BASE_URL).rstrip("/")


def absolute_url(request: HttpRequest | None, path: str) -> str:
    """`path` on the base URL; a URL that is already absolute is returned as it is."""
    if "://" in path:
        return path
    return site_root_url(request) + path
