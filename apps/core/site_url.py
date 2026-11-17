"""Absolute URLs on the request's Wagtail site root, never the Host header (I18N.6, I18N.18).

Canonical, `hreflang`, sitemap and robots.txt URLs are public SEO signals: a forged `Host`
must not be able to put its own domain in them. The site comes from `Site.find_for_request`
(its configured hostname and port, `Site.root_url`). A URL is never relative: with no usable
site, live data raises `ImproperlyConfigured` (the `core.E921` check says so at deploy time) and
anywhere else an error is logged and the placeholder root is used.
"""

import logging

from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from wagtail.models import Site

from .site_checks import PLACEHOLDER_HOSTNAME, hostname_problem, site_is_strict

logger = logging.getLogger(__name__)

FALLBACK_ROOT = "http://localhost"


def site_root_url(request: HttpRequest | None) -> str:
    """The configured root URL of the request's site, without a trailing slash. Never empty."""
    site = Site.find_for_request(request) if request is not None else None
    if site is None:
        site = Site.objects.filter(is_default_site=True).first()
    strict = site_is_strict()
    problem = "No Wagtail Site matches" if site is None else hostname_problem(site.hostname)
    if site is not None and problem is not None and not strict:
        if site.hostname.strip().lower() == PLACEHOLDER_HOSTNAME:
            problem = None  # the installer's own root: fine on a developer machine
        else:
            logger.error("The Wagtail Site hostname %r %s (core.E921).", site.hostname, problem)
    if site is not None and (problem is None or not strict):
        return str(site.root_url).rstrip("/")
    message = f"Absolute URLs need a real Wagtail Site hostname ({problem or 'no site'})."
    if strict:
        raise ImproperlyConfigured(message)
    logger.error("%s Using %s.", message, FALLBACK_ROOT)
    return FALLBACK_ROOT


def absolute_url(request: HttpRequest | None, path: str) -> str:
    """`path` on the site's root URL; a URL that is already absolute is returned as it is."""
    if "://" in path:
        return path
    return site_root_url(request) + path
