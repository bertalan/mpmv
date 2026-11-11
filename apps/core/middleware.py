"""HTTP middleware owned by the platform (thin adapters over apps.core.security)."""

from collections.abc import Callable

from django.conf import settings
from django.http import Http404, HttpRequest
from django.http.response import HttpResponseBase
from django.urls import NoReverseMatch, reverse
from django.utils.cache import add_never_cache_headers
from django.views.defaults import page_not_found

from .http import client_ip, ip_in_networks
from .security import build_permissions_policy


class PermissionsPolicyMiddleware:
    """Add a Permissions-Policy header built from ``settings.PERMISSIONS_POLICY``.

    Django ships CSP, HSTS, COOP, Referrer-Policy and nosniff, but not Permissions-Policy.
    A header already set by a view is left untouched.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponseBase]) -> None:
        self.get_response = get_response
        self.header = build_permissions_policy(getattr(settings, "PERMISSIONS_POLICY", {}))

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        response = self.get_response(request)
        if self.header:
            response.headers.setdefault("Permissions-Policy", self.header)
        return response


class CrossOriginResourcePolicyMiddleware:
    """Add `Cross-Origin-Resource-Policy` from ``settings.CROSS_ORIGIN_RESOURCE_POLICY``.

    Django has no setting for it. ``same-origin`` keeps other sites from embedding our pages,
    scripts and images. COEP (`require-corp`) is not set on purpose: it would block the payment
    provider's embedded element and consent-gated embeds (docs/dev/security-scans.md).
    A header already set by a view is left untouched.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponseBase]) -> None:
        self.get_response = get_response
        self.header = getattr(settings, "CROSS_ORIGIN_RESOURCE_POLICY", "")

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        response = self.get_response(request)
        if self.header:
            response.headers.setdefault("Cross-Origin-Resource-Policy", self.header)
        return response


class PrivateCacheForSignedInUsersMiddleware:
    """Mark responses for a signed-in user as private and uncacheable (SEC.5).

    Pages for a signed-in user show their name, account links and sometimes personal data, and
    many views (Wagtail pages, the account pages) set no cache policy at all, so a shared cache
    could store one user's page and serve it to the next. A ``Cache-Control`` header already set
    by a view is kept. Listed after AuthenticationMiddleware, so ``request.user`` exists.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponseBase]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        response = self.get_response(request)
        if "Cache-Control" in response or settings.SESSION_COOKIE_NAME not in request.COOKIES:
            # No session cookie, no signed-in user: skip the user lookup, which would read the
            # session and add `Vary: Cookie` to every anonymous page.
            return response
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            add_never_cache_headers(response)
        return response


class AdminNetworkAllowlistMiddleware:
    """Answer 404 on the Django admin path to a client outside `ADMIN_ALLOWED_NETWORKS` (OPS.5).

    Defence in depth behind the reverse proxy's allow-list, and fail closed: an empty list denies
    everybody (development and tests list loopback explicitly). The client address is
    `client_ip()`, so a forwarded header counts only from a trusted proxy. Listed after
    LocaleMiddleware and before CommonMiddleware: the refusal is the 404 page of any unknown
    address, in the negotiated language and theme, with the same headers, and `/django-admin`
    without the slash gets no APPEND_SLASH redirect that would give the route away.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponseBase]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        if self._is_admin_path(request.path) and not ip_in_networks(
            client_ip(request), settings.ADMIN_ALLOWED_NETWORKS
        ):
            return page_not_found(request, Http404())
        return self.get_response(request)

    @staticmethod
    def _is_admin_path(path: str) -> bool:
        try:
            bare = reverse("admin:index").rstrip("/")
        except NoReverseMatch:  # a URLconf without the Django admin (some tests)
            return False
        return path == bare or path.startswith(bare + "/")
