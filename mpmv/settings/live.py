"""Production. Everything comes from the environment; nothing has a default."""

from .base import *  # noqa: F403
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

# SEO.6: the anonymous page cache is OFF until MPMV_PAGE_CACHE=1 (enable after a STAGE soak,
# docs/dev/page-cache.md). STAGE inherits this.
PAGE_CACHE_ENABLED = env_bool("MPMV_PAGE_CACHE", default=False)
