"""Production. Everything comes from the environment; nothing has a default."""

from .base import *  # noqa: F403
from .base import MIDDLEWARE as _BASE_MIDDLEWARE
from .base import SECURE_CSP
from .env import env_bool

# TLS terminates at the proxy (spec 12.4), which forwards the original scheme.
# Real customers and vendors: the demo seed (ARC.10) never runs here, even if allowed.
LIVE_DATA = True

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
# The container runtime probes the app directly over HTTP (mpmv.healthcheck, ARC.5); a redirect
# would fail the probe. This exemption is the only way the probe gets through: it sends no
# forged X-Forwarded-Proto header.
SECURE_REDIRECT_EXEMPT = [r"^healthz$", r"^readyz$"]
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_CSP = {**SECURE_CSP, "upgrade-insecure-requests": True}

# SEO.6: anonymous CMS pages are served from the cache (docs/dev/page-cache.md). Off with
# MPMV_PAGE_CACHE=0, the switch to flip if a page ever shows stale content.
PAGE_CACHE_ENABLED = env_bool("MPMV_PAGE_CACHE", default=True)

# Static files are collected into the image at build time (ARC.5) and served by WhiteNoise with
# hashed, compressed names (apps.cms.staticfiles: WhiteNoise's storage, tolerant only of
# Wagtail's references to Wagtail apps left out). A new list, so the base settings stay untouched.
# WhiteNoise answers static requests itself, so it comes right after the middleware that only add
# security headers (Security, CSP, picker nonces, Permissions-Policy, X-Frame-Options): static
# files get the same headers as pages (SEC.5).
_HEADERS_END = _BASE_MIDDLEWARE.index("django.middleware.clickjacking.XFrameOptionsMiddleware") + 1
MIDDLEWARE = [
    *_BASE_MIDDLEWARE[:_HEADERS_END],
    "whitenoise.middleware.WhiteNoiseMiddleware",
    *_BASE_MIDDLEWARE[_HEADERS_END:],
]
# WhiteNoise adds `Access-Control-Allow-Origin: *` to every static file by default. Nothing here
# is read cross-origin, so it stays off (ZAP 10098, docs/dev/security-scans.md).
WHITENOISE_ALLOW_ALL_ORIGINS = False
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "apps.cms.staticfiles.StaticFilesStorage"},
}
