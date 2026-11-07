"""HTTP adapter for API key authentication (CORE.6); CORE.7 plugs it into django-ninja.

`authenticate_request` works with any Django `HttpRequest`; `require_api_key` wraps a plain
Django view and answers 401/403 itself.

The client IP is `REMOTE_ADDR`. Behind the reverse proxy the deployment must set it from the
trusted proxy header (OPS), otherwise the allow-list sees the proxy's address.
"""

import functools
from collections.abc import Callable
from typing import Concatenate

from django.http import HttpRequest, HttpResponse, JsonResponse

from .engine import AuthStatus, Reason
from .models import ApiClient
from .services import AuthResult, authenticate

REALM = "api"
_REQUEST_ATTR = "api_client"


def client_ip(request: HttpRequest) -> str | None:
    ip = request.META.get("REMOTE_ADDR")
    return str(ip) if ip else None


def authenticate_request(request: HttpRequest, required_scope: str) -> AuthResult:
    return authenticate(
        request.headers.get("Authorization"), ip=client_ip(request), required_scope=required_scope
    )


def denial_response(result: AuthResult, required_scope: str) -> JsonResponse:
    """401/403 with a Bearer challenge (RFC 6750); RFC 9457 bodies arrive with CORE.7."""
    response = JsonResponse(
        {"error": str(result.status), "reason": str(result.reason)}, status=result.http_status
    )
    if result.status is AuthStatus.UNAUTHENTICATED:
        challenge = f'Bearer realm="{REALM}"'
        if result.reason is not Reason.MISSING:
            challenge += ', error="invalid_token"'
        response["WWW-Authenticate"] = challenge
    elif result.reason is Reason.INSUFFICIENT_SCOPE:
        response["WWW-Authenticate"] = (
            f'Bearer realm="{REALM}", error="insufficient_scope", scope="{required_scope}"'
        )
    return response


def api_client_of(request: HttpRequest) -> ApiClient:
    """The authenticated client of a request that went through `require_api_key`."""
    client = getattr(request, _REQUEST_ATTR, None)
    if not isinstance(client, ApiClient):
        raise LookupError("request was not authenticated with an API key")
    return client


def require_api_key[**P](
    scope: str,
) -> Callable[
    [Callable[Concatenate[HttpRequest, P], HttpResponse]],
    Callable[Concatenate[HttpRequest, P], HttpResponse],
]:
    """Protect a Django view: valid key with `scope`, else 401/403."""

    def decorator(
        view: Callable[Concatenate[HttpRequest, P], HttpResponse],
    ) -> Callable[Concatenate[HttpRequest, P], HttpResponse]:
        @functools.wraps(view)
        def wrapped(request: HttpRequest, /, *args: P.args, **kwargs: P.kwargs) -> HttpResponse:
            result = authenticate_request(request, scope)
            if not result.ok:
                return denial_response(result, scope)
            setattr(request, _REQUEST_ATTR, result.client)
            return view(request, *args, **kwargs)

        return wrapped

    return decorator
