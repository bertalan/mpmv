"""Dry-run, audit context and the mutation log for API v1 (CORE.7).

Every mutating operation (POST/PUT/PATCH/DELETE) declares `dry_run: DryRun = False`, passes it
to its service and answers with `changeset_out(...)`. `apps.api.v1.checks` refuses a mutating
operation without the parameter, so `?dry_run=true` can never be silently ignored.
"""

import functools
import logging
import time
from collections.abc import Callable
from typing import Annotated, Any, Literal

from django.http import HttpRequest
from django.http.response import HttpResponseBase
from ninja import Schema
from ninja.params.functions import Query
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from apps.api.auth import client_ip, known_client_of
from apps.api.models import ApiClient
from apps.api.responses import REQUEST_ID_HEADER, assign_request_id, request_id_of
from apps.api.services import record_mutation_call
from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.models import AuditLog

logger = logging.getLogger("apps.api")

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
DRY_RUN_PARAM = "dry_run"

DryRun = Annotated[
    bool,
    Query(
        False,
        alias=DRY_RUN_PARAM,
        description="Validate and return the ChangeSet without applying anything.",
    ),
]

_BOOL = TypeAdapter(bool)


class ChangeSummary(Schema):
    create: int
    update: int
    delete: int


class ChangeOut(Schema):
    op: Literal["create", "update", "delete"]
    model: str
    key: str
    fields: dict[str, list[Any]]


class ChangeSetOut(Schema):
    """The diff of a mutation: what would change (dry-run) or what changed."""

    dry_run: bool
    summary: ChangeSummary
    changes: list[ChangeOut]


def changeset_out(changeset: ChangeSet, *, dry_run: bool) -> dict[str, Any]:
    return {"dry_run": dry_run, **changeset.to_dict()}


def audit_context(request: HttpRequest) -> AuditContext:
    """Who is acting, for services: the API client and its key prefix, IP, request id."""
    client = known_client_of(request)
    if client is None:
        raise LookupError("request was not authenticated with an API key")
    return AuditContext(
        actor_label=f"api:{client.name} [{client.key_prefix}]"[:150],
        source=AuditLog.Source.API,
        ip=client_ip(request),
        request_id=request_id_of(request),
    )


def requested_dry_run(request: HttpRequest) -> bool:
    """`?dry_run=` as the operation parses it; an invalid value counts as False (422 anyway)."""
    raw = request.GET.get(DRY_RUN_PARAM)
    if raw is None:
        return False
    try:
        return _BOOL.validate_python(raw)
    except PydanticValidationError:
        return False


def _log_call(request: HttpRequest, client: ApiClient, status: int, started: float) -> None:
    match = request.resolver_match
    try:
        record_mutation_call(
            client,
            request_id=request_id_of(request),
            method=request.method or "",
            path=request.path,
            route=match.route if match else "",
            dry_run=requested_dry_run(request),
            status_code=status,
            ip=client_ip(request),
            duration_ms=round((time.monotonic() - started) * 1000),
        )
    except Exception:
        # The change itself is already committed and audited; losing the call row must not
        # turn a successful response into an error that invites a retry.
        logger.exception("Could not record the API mutation log for %s", request.path)


def track_calls(
    run: Callable[..., HttpResponseBase],
) -> Callable[..., HttpResponseBase]:
    """Ninja view decorator: request id on every call, mutation log for mutating ones."""

    @functools.wraps(run)
    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        started = time.monotonic()
        request_id = assign_request_id(request)
        response = run(request, *args, **kwargs)
        response[REQUEST_ID_HEADER] = request_id
        client = known_client_of(request)
        if request.method in MUTATING_METHODS and client is not None:
            _log_call(request, client, response.status_code, started)
        return response

    return wrapped
