"""HTTP middleware owned by the platform (thin adapters over apps.core.security)."""

from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest
from django.http.response import HttpResponseBase

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
