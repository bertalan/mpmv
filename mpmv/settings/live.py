"""Production. Everything comes from the environment; nothing has a default."""

from .base import *  # noqa: F403
from .base import SECURE_CSP
from .env import env_bool

# TLS terminates at the proxy (spec 12.4), which forwards the original scheme.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
# The container runtime probes the app directly over HTTP; a redirect would fail the probe.
SECURE_REDIRECT_EXEMPT = [r"^healthz$"]
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_CSP = {**SECURE_CSP, "upgrade-insecure-requests": True}
