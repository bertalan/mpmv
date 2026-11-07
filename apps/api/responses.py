"""Django HTTP helpers shared by the API adapters: problem responses and the request id."""

import uuid

from django.http import HttpRequest, JsonResponse

from .problems import PROBLEM_CONTENT_TYPE, Problem

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_ATTR = "api_request_id"


def assign_request_id(request: HttpRequest) -> str:
    """Give the request a fresh id (never taken from the client, so it cannot be forged)."""
    request_id = uuid.uuid4().hex
    setattr(request, _REQUEST_ID_ATTR, request_id)
    return request_id


def request_id_of(request: HttpRequest) -> str:
    request_id = getattr(request, _REQUEST_ID_ATTR, "")
    return request_id if isinstance(request_id, str) else ""


def problem_response(
    request: HttpRequest, problem: Problem, headers: dict[str, str] | None = None
) -> JsonResponse:
    """An `application/problem+json` response for `problem`, with instance and request id."""
    extra = {"request_id": rid} if (rid := request_id_of(request)) else {}
    body = problem.to_dict(instance=request.path, **extra)
    response = JsonResponse(body, status=problem.status, content_type=PROBLEM_CONTENT_TYPE)
    for name, value in (headers or {}).items():
        response[name] = value
    if rid:
        response[REQUEST_ID_HEADER] = rid
    return response
