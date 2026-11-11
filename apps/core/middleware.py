"""HTTP middleware owned by the platform (thin adapters over apps.core.security)."""

import re
import uuid
from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest
from django.http.response import HttpResponseBase
from django.utils.cache import add_never_cache_headers

from .observability import reset_request_id, set_request_id
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


_REQUEST_ID = re.compile(r"[A-Za-z0-9-]{8,64}")


class RequestIdMiddleware:
    """Give every request an id for the logs and answer it in `X-Request-ID` (OPS.2).

    A proxy's id is kept when it is 8 to 64 letters, digits or dashes, else a new one is made, so
    a hostile header cannot inject log lines or response headers.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponseBase]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        inbound = request.META.get("HTTP_X_REQUEST_ID", "")
        request_id = inbound if _REQUEST_ID.fullmatch(inbound) else uuid.uuid4().hex
        token = set_request_id(request_id)
        try:
            response = self.get_response(request)
        finally:
            reset_request_id(token)
        response.headers["X-Request-ID"] = request_id
        return response
