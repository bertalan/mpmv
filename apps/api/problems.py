"""RFC 9457 problem details for API errors (CORE.7). Pure functions: no Django, no ORM.

Every error body is an `application/problem+json` object with the standard members `type`,
`title`, `status`, `detail` (when there is one) and `instance` (the request path), plus:

- `code`: a stable, machine-readable identifier (snake_case). Clients branch on it.
- `type`: `urn:mpmv:problem:<code>`. A URN, not a URL: the identifier must not depend on a
  hostname, and RFC 9457 §3.1.1 allows non-resolvable type URIs.
- extension members specific to the problem, e.g. `errors` for validation, `required_scope`
  for a missing scope, `request_id` to correlate with the logs.

Details never echo internals (exception messages, SQL, submitted values).
"""

import http
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from .engine import AuthStatus, Decision, Reason

PROBLEM_CONTENT_TYPE: Final = "application/problem+json"
TYPE_PREFIX: Final = "urn:mpmv:problem:"
STANDARD_MEMBERS: Final = frozenset({"type", "title", "status", "detail", "instance", "code"})

DEFAULT_CODES: Final[Mapping[int, str]] = {
    400: "bad_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
}

_AUTH_DETAILS: Final[Mapping[Reason, str]] = {
    Reason.MISSING: "Send an API key as 'Authorization: Bearer <key>'.",
    Reason.MALFORMED: "The Authorization header is not a valid Bearer API key.",
    Reason.INVALID: "The API key is not valid.",
    Reason.REVOKED: "The API key has been revoked.",
    Reason.EXPIRED: "The API key has expired.",
    Reason.IP_NOT_ALLOWED: "The API key may not be used from this address.",
    Reason.INSUFFICIENT_SCOPE: "The API key lacks the scope this operation requires.",
    Reason.VENDOR_CLOSED: "The vendor this API key acts for is closed.",
    Reason.VENDOR_SUSPENDED: "The vendor of this API key is suspended: the key may only read.",
    Reason.VENDOR_NOT_ACTIVE: (
        "The vendor of this API key is not active yet: the key may only read."
    ),
}


def _phrase(status: int) -> str:
    try:
        return http.HTTPStatus(status).phrase
    except ValueError:
        return "Error"


def _default_code(status: int) -> str:
    if status in DEFAULT_CODES:
        return DEFAULT_CODES[status]
    return "server_error" if status >= 500 else "client_error"


@dataclass(frozen=True)
class Problem:
    status: int
    code: str
    title: str
    detail: str = ""
    extensions: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self, *, instance: str | None = None, **extra: Any) -> dict[str, Any]:
        """The JSON body; `extra` adds request-level members such as `request_id`."""
        body: dict[str, Any] = {
            "type": f"{TYPE_PREFIX}{self.code}",
            "title": self.title,
            "status": self.status,
        }
        if self.detail:
            body["detail"] = self.detail
        if instance:
            body["instance"] = instance
        body["code"] = self.code
        body.update(self.extensions)
        body.update(_checked(extra))
        return body


def _checked(extensions: Mapping[str, Any]) -> dict[str, Any]:
    clashing = sorted(STANDARD_MEMBERS.intersection(extensions))
    if clashing:
        raise ValueError(f"extension members cannot override standard members: {clashing}")
    return dict(extensions)


def problem(
    status: int,
    *,
    code: str | None = None,
    detail: str = "",
    title: str | None = None,
    **extensions: Any,
) -> Problem:
    """A problem for `status`; code and title default from the status."""
    return Problem(
        status=status,
        code=code or _default_code(status),
        title=title or _phrase(status),
        detail=detail,
        extensions=_checked(extensions),
    )


def validation_problem(errors: Iterable[Mapping[str, Any]]) -> Problem:
    """422 with `errors: [{loc, msg, type}]`; submitted input and context are dropped."""
    cleaned = [
        {
            "loc": list(e.get("loc", [])),
            "msg": str(e.get("msg", "")),
            "type": str(e.get("type", "")),
        }
        for e in errors
    ]
    return problem(422, detail="The request is not valid.", errors=cleaned)


def rate_limit_problem() -> Problem:
    """429 for a key over its rate limit (CORE.12); the delay goes in the `Retry-After` header."""
    return problem(
        429,
        detail=(
            "This API key has made too many requests; retry after the delay in the "
            "Retry-After header."
        ),
    )


def auth_problem(decision: Decision, required_scope: str | None) -> Problem:
    """401/403 for a refused API key; `code` is the refusal reason."""
    if decision.status is AuthStatus.OK or decision.reason is None:
        raise ValueError("an allowed decision is not a problem")
    extensions: dict[str, Any] = {}
    if decision.reason is Reason.INSUFFICIENT_SCOPE and required_scope is not None:
        extensions["required_scope"] = required_scope
    return problem(
        decision.http_status,
        code=str(decision.reason),
        detail=_AUTH_DETAILS[decision.reason],
        **extensions,
    )
