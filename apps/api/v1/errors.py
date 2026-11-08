"""Exception → RFC 9457 problem mapping for API v1 (400/401/403/404/409/422/429/500).

Details never carry exception messages: they may contain SQL, internal ids or submitted values.
Unexpected exceptions are logged with their traceback and answered with a generic 500.
"""

import logging
from collections.abc import Callable
from typing import Any, cast

from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from django.http import Http404, HttpRequest, HttpResponse
from ninja import NinjaAPI
from ninja.errors import HttpError
from ninja.errors import ValidationError as NinjaValidationError

from apps.api.auth import denial_response, rate_limited_response
from apps.api.engine import Reason, deny, vendor_write_refusal
from apps.api.problems import Problem, problem, validation_problem
from apps.api.responses import problem_response
from apps.api.services import AuthResult
from apps.vendors.engine import VendorCannotWrite

from .security import ApiAuthError, ApiRateLimited

logger = logging.getLogger("apps.api")

_HTTP_ERROR_DETAILS = {
    400: "The request could not be parsed.",
    429: "Too many requests; retry after the delay in the Retry-After header.",
}


class ApiProblem(Exception):
    """Raise from an endpoint to answer with a specific problem (status, code, extensions)."""

    def __init__(self, status: int, *, code: str, detail: str = "", **extensions: Any) -> None:
        super().__init__(code)
        self.problem = problem(status, code=code, detail=detail, **extensions)


def _django_validation_errors(exc: DjangoValidationError) -> list[dict[str, Any]]:
    if hasattr(exc, "error_dict"):
        return [
            {
                "loc": [] if name == "__all__" else [name],
                "msg": message,
                "type": error.code or "invalid",
            }
            for name, errors in exc.error_dict.items()
            for error in errors
            for message in error.messages
        ]
    return [
        {"loc": [], "msg": message, "type": error.code or "invalid"}
        for error in exc.error_list
        for message in error.messages
    ]


def _respond(request: HttpRequest, built: Problem) -> HttpResponse:
    return problem_response(request, built)


def _on_auth(request: HttpRequest, exc: ApiAuthError) -> HttpResponse:
    return denial_response(request, exc.result, exc.required_scope)


def _on_rate_limited(request: HttpRequest, exc: ApiRateLimited) -> HttpResponse:
    return rate_limited_response(request, exc.decision)


def _on_http_error(request: HttpRequest, exc: HttpError) -> HttpResponse:
    status = exc.status_code
    return _respond(request, problem(status, detail=_HTTP_ERROR_DETAILS.get(status, "")))


def _on_ninja_validation(request: HttpRequest, exc: NinjaValidationError) -> HttpResponse:
    return _respond(request, validation_problem(exc.errors))


def _on_django_validation(request: HttpRequest, exc: DjangoValidationError) -> HttpResponse:
    return _respond(request, validation_problem(_django_validation_errors(exc)))


def _on_not_found(request: HttpRequest, exc: Exception) -> HttpResponse:
    return _respond(request, problem(404, detail="The resource does not exist."))


def _on_vendor_cannot_write(request: HttpRequest, exc: VendorCannotWrite) -> HttpResponse:
    """The vendor stopped being active between authentication and the write (CORE.14): the
    same refusal the key would get on its next request."""
    refusal = vendor_write_refusal(exc.status) or Reason.VENDOR_NOT_ACTIVE
    return denial_response(request, AuthResult(deny(refusal), None), None)


def _on_permission_denied(request: HttpRequest, exc: Exception) -> HttpResponse:
    return _respond(request, problem(403, detail="This operation is not allowed."))


def _on_integrity(request: HttpRequest, exc: Exception) -> HttpResponse:
    logger.info("API conflict on %s: %s", request.path, type(exc).__name__)
    detail = "The request conflicts with the current state of the resource."
    return _respond(request, problem(409, detail=detail))


def _on_problem(request: HttpRequest, exc: ApiProblem) -> HttpResponse:
    return _respond(request, exc.problem)


def _on_unexpected(request: HttpRequest, exc: Exception) -> HttpResponse:
    logger.error("Unhandled API error on %s %s", request.method, request.path, exc_info=exc)
    return _respond(request, problem(500, detail="An unexpected error occurred."))


_HANDLERS: tuple[tuple[type[Exception], Callable[[HttpRequest, Any], HttpResponse]], ...] = (
    (ApiAuthError, _on_auth),
    (ApiRateLimited, _on_rate_limited),  # the per-key rate limit (CORE.12)
    (HttpError, _on_http_error),  # also ninja's own 401/403 and the 429 of throttling
    (NinjaValidationError, _on_ninja_validation),
    (DjangoValidationError, _on_django_validation),
    (Http404, _on_not_found),
    (ObjectDoesNotExist, _on_not_found),
    (VendorCannotWrite, _on_vendor_cannot_write),
    (PermissionDenied, _on_permission_denied),
    (IntegrityError, _on_integrity),
    (ApiProblem, _on_problem),
    (Exception, _on_unexpected),
)


def install_problem_handlers(api: NinjaAPI) -> None:
    """Replace django-ninja's default handlers; lookups follow the exception's MRO."""
    for exc_class, handler in _HANDLERS:
        # ninja types the handler's argument as `exc | type[exc]`; it always passes an instance.
        api.add_exception_handler(exc_class, cast(Any, handler))


def not_found(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
    """Catch-all for unknown paths under /api/v1/: a 404 problem instead of the HTML page."""
    return _respond(request, problem(404, detail="No such API endpoint."))
