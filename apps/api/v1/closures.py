"""Vendor closures API of API v1 (VEN.20): `/api/v1/vendors/{vendor_id}/closures`.

A thin adapter over `apps.vendors.services` (`add_closure`, `update_closure`, `delete_closure`):
the payload becomes the service's input, the service validates, applies or dry-runs, and audits.
`vendor_id` is the vendor's public id. The caller's key scopes the data (CORE.13): a vendor key
reaches its own vendor only; another vendor, and another vendor's closure under its own id, are
a 404, as for one that does not exist. A platform key reaches every vendor. Scopes:
`closures:read` and `closures:write`. A range that has ended is history: changing or deleting it
is a 422 problem.
"""

import datetime as dt
import uuid
from typing import Annotated, Any

from django.core.exceptions import ValidationError
from django.http import HttpRequest
from ninja import Body, Router, Schema, Status
from pydantic import ConfigDict, Field

from apps.api.auth import api_client_of
from apps.vendors import services
from apps.vendors.engine import CLOSURE_MAX_NOTICE_DAYS
from apps.vendors.models import Vendor, VendorClosure
from apps.vendors.scoping import get_for_actor_or_404

from .mutations import ChangeSetOut, DryRun, audit_context, changeset_out
from .security import ApiKey

router = Router(tags=["closures"])

READ = "closures:read"
WRITE = "closures:write"


class ClosureCreateIn(Schema):
    """A closed range, first and last day included, as calendar dates in the platform's time
    zone."""

    model_config = ConfigDict(extra="forbid")

    start_date: dt.date | None = Field(default=None, description="Required.")
    end_date: dt.date | None = Field(default=None, description="Required.")
    repeats_yearly: bool = False
    notice_days: int = Field(default=14, ge=0, le=CLOSURE_MAX_NOTICE_DAYS)


class ClosurePatchIn(Schema):
    """Any subset of the fields: `PATCH` changes the ones sent."""

    model_config = ConfigDict(extra="forbid")

    start_date: dt.date | None = None
    end_date: dt.date | None = None
    repeats_yearly: bool | None = None
    notice_days: int | None = Field(default=None, ge=0, le=CLOSURE_MAX_NOTICE_DAYS)


class ClosureOut(Schema):
    public_id: uuid.UUID
    vendor: uuid.UUID
    start_date: dt.date
    end_date: dt.date
    repeats_yearly: bool
    notice_days: int
    ended: bool


class ClosureList(Schema):
    count: int
    items: list[ClosureOut]


def _vendor(request: HttpRequest, vendor_id: uuid.UUID) -> Vendor:
    return get_for_actor_or_404(Vendor.objects, api_client_of(request), public_id=vendor_id)


def _closure(request: HttpRequest, vendor_id: uuid.UUID, closure_id: uuid.UUID) -> VendorClosure:
    """The vendor first, so another vendor's id is a 404 whatever the closure id; then the
    closure inside that vendor, so a closure under the wrong vendor id is a 404 too."""
    vendor = _vendor(request, vendor_id)
    return get_for_actor_or_404(
        VendorClosure.objects.select_related("vendor"),
        api_client_of(request),
        public_id=closure_id,
        vendor=vendor,
    )


def _out(closure: VendorClosure) -> dict[str, Any]:
    return {
        "public_id": closure.public_id,
        "vendor": closure.vendor.public_id,
        "start_date": closure.start_date,
        "end_date": closure.end_date,
        "repeats_yearly": closure.repeats_yearly,
        "notice_days": closure.notice_days,
        "ended": not closure.repeats_yearly and closure.end_date < services.business_today(),
    }


@router.get(
    "/{vendor_id}/closures",
    response=ClosureList,
    auth=ApiKey(READ),
    summary="List a vendor's closures",
    url_name="vendor_closures",
)
def list_closures(request: HttpRequest, vendor_id: uuid.UUID) -> dict[str, Any]:
    """Every range of the vendor, history included, by start date."""
    vendor = _vendor(request, vendor_id)
    rows = VendorClosure.objects.filter(vendor=vendor).select_related("vendor")
    items = [_out(closure) for closure in rows.order_by("start_date", "end_date")]
    return {"count": len(items), "items": items}


@router.post(
    "/{vendor_id}/closures",
    response={200: ChangeSetOut, 201: ChangeSetOut},
    auth=ApiKey(WRITE),
    summary="Add a closure",
    url_name="vendor_closures",
)
def create_closure(
    request: HttpRequest,
    vendor_id: uuid.UUID,
    payload: Annotated[ClosureCreateIn | None, Body()] = None,
    dry_run: DryRun = False,
) -> Status[dict[str, Any]]:
    """201 with the ChangeSet; 200 with it on `?dry_run=true`. An invalid range is a 422 problem
    naming the fields. It takes effect at once and needs no review."""
    # The vendor is looked up first, so another vendor is a 404 whatever the body.
    vendor = _vendor(request, vendor_id)
    body = payload or ClosureCreateIn()
    start, end = body.start_date, body.end_date
    if start is None or end is None:
        raise ValidationError(
            {
                name: ["This field is required."]
                for name, value in (("start_date", start), ("end_date", end))
                if value is None
            }
        )
    changes = services.add_closure(
        vendor,
        start,
        end,
        repeats_yearly=body.repeats_yearly,
        notice_days=body.notice_days,
        ctx=audit_context(request),
        today=services.business_today(),
        dry_run=dry_run,
    )
    return Status(200 if dry_run else 201, changeset_out(changes, dry_run=dry_run))


@router.patch(
    "/{vendor_id}/closures/{closure_id}",
    response=ChangeSetOut,
    auth=ApiKey(WRITE),
    summary="Change a closure",
    url_name="vendor_closure",
)
def update_closure(
    request: HttpRequest,
    vendor_id: uuid.UUID,
    closure_id: uuid.UUID,
    payload: Annotated[ClosurePatchIn | None, Body()] = None,
    dry_run: DryRun = False,
) -> dict[str, Any]:
    # Looked up first, so another vendor's closure is a 404 whatever the body.
    closure = _closure(request, vendor_id, closure_id)
    changes = services.update_closure(
        closure,
        **({} if payload is None else payload.model_dump(exclude_unset=True, exclude_none=True)),
        ctx=audit_context(request),
        today=services.business_today(),
        dry_run=dry_run,
    )
    return changeset_out(changes, dry_run=dry_run)


@router.delete(
    "/{vendor_id}/closures/{closure_id}",
    response=ChangeSetOut,
    auth=ApiKey(WRITE),
    summary="Remove a closure",
    url_name="vendor_closure",
)
def delete_closure(
    request: HttpRequest, vendor_id: uuid.UUID, closure_id: uuid.UUID, dry_run: DryRun = False
) -> dict[str, Any]:
    closure = _closure(request, vendor_id, closure_id)
    changes = services.delete_closure(
        closure, ctx=audit_context(request), today=services.business_today(), dry_run=dry_run
    )
    return changeset_out(changes, dry_run=dry_run)
