"""Absolute URLs on the request's Wagtail site root, never the Host header (I18N.6).

Canonical, `hreflang`, sitemap and robots.txt URLs are public SEO signals: a forged `Host`
must not be able to put its own domain in them. The site comes from `Site.find_for_request`
(its configured hostname and port, `Site.root_url`).
"""

from django.http import HttpRequest
from wagtail.models import Site


def site_root_url(request: HttpRequest | None) -> str:
    """The configured root URL of the request's site, without a trailing slash; empty when no
    site matches (the URL then stays a path)."""
    site = Site.find_for_request(request) if request is not None else None
    return site.root_url.rstrip("/") if site is not None else ""


def absolute_url(request: HttpRequest | None, path: str) -> str:
    """`path` on the site's root URL; a URL that is already absolute is returned as it is."""
    if "://" in path:
        return path
    return site_root_url(request) + path
