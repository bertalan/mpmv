"""A test-only API v1 instance: the production setup plus endpoints that exercise the skeleton.

Built with the same `create_api()` as the real API, so errors, auth, dry-run and the mutation
log behave exactly as in production. Never mounted outside the tests.
"""

import uuid

from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from django.http import Http404, HttpRequest
from ninja import Router, Schema
from ninja.throttling import AuthRateThrottle
from pydantic import Field

from apps.api.auth import api_client_of
from apps.api.models import ApiClient
from apps.api.services import revoke_api_client
from apps.api.v1.api import create_api
from apps.api.v1.endpoints import router as v1_router
from apps.api.v1.errors import ApiProblem
from apps.api.v1.mutations import ChangeSetOut, DryRun, audit_context, changeset_out
from apps.api.v1.security import ApiKey
from apps.vendors.scoping import get_for_actor_or_404

LEAKY_DETAIL = "internal detail that must never reach the client"

router = Router(tags=["test"])


@router.post("/clients/{public_id}/revoke", response=ChangeSetOut, auth=ApiKey("clients:write"))
def revoke_client(request: HttpRequest, public_id: uuid.UUID, dry_run: DryRun = False) -> object:
    """A real mutating service behind a test-only route: proves dry-run, audit and call log.

    The target is found within the caller's actor scope, as in every API v1 operation on vendor
    data (CORE.13): a vendor key reaches only its own vendor's clients.
    """
    client = get_for_actor_or_404(ApiClient, api_client_of(request), public_id=public_id)
    changeset = revoke_api_client(client, ctx=audit_context(request), dry_run=dry_run)
    return changeset_out(changeset, dry_run=dry_run)


class EchoIn(Schema):
    name: str
    quantity: int = Field(gt=0)


@router.post("/echo", auth=ApiKey("test:write"))
def echo(request: HttpRequest, payload: EchoIn, dry_run: DryRun = False) -> dict[str, object]:
    return {"name": payload.name, "quantity": payload.quantity, "dry_run": dry_run}


@router.get("/errors/{kind}", auth=ApiKey("test:read"))
def raise_error(request: HttpRequest, kind: str) -> None:
    """Raise one kind of failure, to check how each one is rendered."""
    if kind == "http404":
        raise Http404(LEAKY_DETAIL)
    if kind == "does-not-exist":
        ApiClient.objects.get(public_id=uuid.uuid4())
    if kind == "permission":
        raise PermissionDenied(LEAKY_DETAIL)
    if kind == "integrity":
        raise IntegrityError(LEAKY_DETAIL)
    if kind == "django-validation":
        raise DjangoValidationError({"name": ["Name is too long."]})
    if kind == "problem":
        raise ApiProblem(409, code="already_exists", detail="SKU A-1 already exists.", sku="A-1")
    raise RuntimeError(LEAKY_DETAIL)


@router.get("/throttled", auth=ApiKey("test:read"), throttle=AuthRateThrottle("1/min"))
def throttled(request: HttpRequest) -> dict[str, bool]:
    return {"ok": True}


test_api = create_api(urls_namespace="api-v1-test")
test_api.add_router("", v1_router)
test_api.add_router("/test", router)
