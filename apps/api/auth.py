"""HTTP adapter for API key authentication (CORE.6); CORE.7 plugs it into django-ninja.

`authenticate_request` works with any Django `HttpRequest`; `require_api_key` wraps a plain
Django view and answers 401/403 itself, as RFC 9457 problem details. API v1 (django-ninja)
uses the same functions through `apps.api.v1.security.ApiKey`.

The client IP comes from `apps.core.http.client_ip`: the trusted reverse proxy's header, or
`REMOTE_ADDR` (CORE.5).

Once the key is accepted, the call is counted against the key's rate limit (CORE.12,
`services.count_api_call`); over the limit the answer is a 429 problem with `Retry-After`.
Refused keys (401/403) are not counted: they never reach an operation.
"""

import functools
from collections.abc import Callable
from typing import Concatenate

from django.http import HttpRequest, HttpResponse, JsonResponse

from apps.core.http import client_ip

from .engine import RateDecision, bearer_challenge, is_write
from .models import ApiClient
from .problems import auth_problem, rate_limit_problem
from .responses import problem_response
from .services import AuthResult, authenticate, count_api_call

REALM = "api"
_REQUEST_ATTR = "api_client"


def authenticate_request(request: HttpRequest, required_scope: str | None) -> AuthResult:
    """The method decides read or write: a suspended vendor's key only reads (CORE.14)."""
    return authenticate(
        request.headers.get("Authorization"),
        ip=client_ip(request),
        required_scope=required_scope,
        write=request.method is None or is_write(request.method),
    )


def denial_response(
    request: HttpRequest, result: AuthResult, required_scope: str | None
) -> JsonResponse:
    """401/403 as RFC 9457 problem details, with a Bearer challenge (RFC 6750) when due."""
    challenge = bearer_challenge(result.decision, required_scope, realm=REALM)
    headers = {"WWW-Authenticate": challenge} if challenge else {}
    return problem_response(request, auth_problem(result.decision, required_scope), headers)


def rate_limit(client: ApiClient, required_scope: str | None) -> RateDecision:
    """Count the call of an accepted key against its rate limit (CORE.12)."""
    return count_api_call(client, required_scope)


def rate_limited_response(request: HttpRequest, decision: RateDecision) -> JsonResponse:
    """429 as an RFC 9457 problem, with `Retry-After` in seconds (RFC 9110 §10.2.3)."""
    headers = {"Retry-After": str(decision.retry_after)}
    return problem_response(request, rate_limit_problem(), headers)


def remember_client(request: HttpRequest, client: ApiClient | None) -> None:
    """Attach the key's client to the request (also when refused with 403, for the call log)."""
    if client is not None:
        setattr(request, _REQUEST_ATTR, client)


def known_client_of(request: HttpRequest) -> ApiClient | None:
    client = getattr(request, _REQUEST_ATTR, None)
    return client if isinstance(client, ApiClient) else None


def api_client_of(request: HttpRequest) -> ApiClient:
    """The authenticated client of a request that went through `require_api_key`."""
    client = known_client_of(request)
    if client is None:
        raise LookupError("request was not authenticated with an API key")
    return client


def require_api_key[**P](
    scope: str,
) -> Callable[
    [Callable[Concatenate[HttpRequest, P], HttpResponse]],
    Callable[Concatenate[HttpRequest, P], HttpResponse],
]:
    """Protect a Django view: valid key with `scope`, else 401/403; 429 over the rate limit."""

    def decorator(
        view: Callable[Concatenate[HttpRequest, P], HttpResponse],
    ) -> Callable[Concatenate[HttpRequest, P], HttpResponse]:
        @functools.wraps(view)
        def wrapped(request: HttpRequest, /, *args: P.args, **kwargs: P.kwargs) -> HttpResponse:
            result = authenticate_request(request, scope)
            if not result.ok:
                return denial_response(request, result, scope)
            remember_client(request, result.client)
            limited = rate_limit(api_client_of(request), scope)
            if not limited.allowed:
                return rate_limited_response(request, limited)
            return view(request, *args, **kwargs)

        return wrapped

    return decorator
