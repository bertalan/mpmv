"""HTTP middleware owned by the platform (thin adapters over apps.core.security)."""

from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest
from django.http.response import HttpResponseBase
from django.utils.cache import add_never_cache_headers

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
