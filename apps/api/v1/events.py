"""Vendor events API of API v1 (EVT.5): `/api/v1/events`.

A thin adapter over `apps.events.services` (EVT.3): the payload becomes the service's input, the
service validates, applies or dry-runs, and audits. The caller's key scopes the data (CORE.13):
a vendor key lists, reads and writes its own vendor's events only; another vendor's event is a
404, as for one that does not exist. A platform key reads every event and authors none (it
names no vendor to act for). Scopes: `events:read` and `events:write`.

Writes save a draft revision (`POST`, `PATCH`); `submit` sends the latest revision to review or
publishes it, `cancel` withdraws a live event. The body is StreamField JSON limited to the
vendor-safe blocks (paragraph, figure, gallery, cta, faq); the service refuses the rest.
"""

import datetime as dt
import uuid
from decimal import Decimal
from typing import Annotated, Any

from django.core.exceptions import ValidationError
from django.http import HttpRequest
from ninja import Query, Router, Schema, Status
from pydantic import ConfigDict, Field

from apps.api.auth import api_client_of
from apps.catalog.models import Product
from apps.events import services
from apps.events.models import EventPage
from apps.vendors.scoping import get_for_actor_or_404

from .mutations import ChangeSetOut, DryRun, audit_context, changeset_out
from .security import ApiKey
from .text import PlainText

router = Router(tags=["events"])

READ = "events:read"
WRITE = "events:write"
MAX_PAGE_SIZE = 100


class EventIn(Schema):
    """Any subset of the editable fields: `PATCH` changes the ones sent; `POST` needs the
    mandatory ones (the service answers 422 naming the missing).

    The vendor, the language and the page state are not input; geocoding provenance is set by
    the platform.
    """

    model_config = ConfigDict(extra="forbid")

    title: PlainText = Field("", max_length=255)
    slug: PlainText = ""
    summary: PlainText = Field("", max_length=300)
    event_type: PlainText = Field("", description="promotion, fair or other.")
    all_day: bool = False
    starts_at: dt.datetime | None = None
    ends_at: dt.datetime | None = None
    start_date: dt.date | None = None
    end_date: dt.date | None = None
    timezone: PlainText = Field("", description="IANA name, e.g. Europe/Rome.")
    rrule: PlainText = Field("", description="RFC 5545 RRULE, e.g. FREQ=YEARLY;COUNT=3.")
    exdates: list[dt.datetime] = Field(default_factory=list)
    exdate_days: list[dt.date] = Field(default_factory=list)
    attendance: PlainText = Field("", description="in_person, online or mixed.")
    online_url: PlainText = ""
    venue_name: PlainText = ""
    street: PlainText = ""
    postcode: PlainText = ""
    city: PlainText = ""
    region: PlainText = ""
    country: PlainText = ""
    booth: PlainText = ""
    latitude: Decimal | None = Field(None, max_digits=9, decimal_places=6)
    longitude: Decimal | None = Field(None, max_digits=9, decimal_places=6)
    body: list[dict[str, Any]] = Field(
        default_factory=list,
        description="StreamField blocks: [{type, value}] with vendor-safe types only.",
    )
    products: list[uuid.UUID] = Field(
        default_factory=list, description="Public ids of the vendor's own products to link."
    )


class EventItem(Schema):
    public_id: uuid.UUID
    vendor: uuid.UUID | None
    title: str
    slug: str
    language: str
    event_type: str
    status: str
    live: bool
    starts_at: dt.datetime | None
    start_date: dt.date | None


class EventList(Schema):
    count: int
    items: list[EventItem]


class EventOut(EventItem):
    """The latest draft revision, which may be ahead of the published page."""

    summary: str
    all_day: bool
    ends_at: dt.datetime | None
    end_date: dt.date | None
    timezone: str
    rrule: str
    exdates: list[dt.datetime]
    exdate_days: list[dt.date]
    attendance: str
    online_url: str
    venue_name: str
    street: str
    postcode: str
    city: str
    region: str
    country: str
    booth: str
    latitude: Decimal | None
    longitude: Decimal | None
    body: list[dict[str, Any]]
    products: list[uuid.UUID]
    cancelled_at: dt.datetime | None


class ListQuery(Schema):
    limit: int = Field(MAX_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    offset: int = Field(0, ge=0)


def _item(page: EventPage) -> dict[str, Any]:
    return {
        "public_id": page.public_id,
        "vendor": None if page.vendor is None else page.vendor.public_id,
        "title": page.title,
        "slug": page.slug,
        "language": page.locale.language_code,
        "event_type": page.event_type,
        "status": page.status,
        "live": page.live,
        "starts_at": page.starts_at,
        "start_date": page.start_date,
    }


def _detail(page: EventPage) -> dict[str, Any]:
    draft: EventPage = page.get_latest_revision_as_object()
    detail = _item(page) | {
        name: getattr(draft, name)
        for name in (
            "title",
            "event_type",
            "starts_at",
            "start_date",
            "summary",
            "all_day",
            "ends_at",
            "end_date",
            "timezone",
            "rrule",
            "exdates",
            "exdate_days",
            "attendance",
            "online_url",
            "venue_name",
            "street",
            "postcode",
            "city",
            "region",
            "country",
            "booth",
            "latitude",
            "longitude",
            "cancelled_at",
        )
    }
    detail["body"] = draft.body.get_prep_value()
    detail["products"] = [p.public_id for p in draft.products.all()]
    return detail


def _event(request: HttpRequest, public_id: uuid.UUID) -> EventPage:
    return get_for_actor_or_404(
        EventPage.objects.select_related("vendor", "locale"),
        api_client_of(request),
        public_id=public_id,
    )


def _input(request: HttpRequest, payload: EventIn) -> dict[str, Any]:
    """The service input: the fields the client sent, products by primary key."""
    data = payload.model_dump(exclude_unset=True)
    if "products" in data:
        ids: list[uuid.UUID] = data["products"]
        # Scoped to the key's vendor: another vendor's product is "not found", like a missing one.
        found = {
            product.public_id: product.pk
            for product in Product.objects.for_actor(api_client_of(request)).filter(
                public_id__in=ids
            )
        }
        if len(found) != len(set(ids)):
            raise ValidationError({"products": "Choose your own existing products."})
        data["products"] = [found[public_id] for public_id in ids]
    return data


@router.get("", response=EventList, auth=ApiKey(READ), summary="List events")
def list_events(request: HttpRequest, query: Annotated[ListQuery, Query()]) -> dict[str, Any]:
    """The key's events, newest first (all languages). Items show the published page; `GET` one
    event for its latest draft."""
    pages = (
        EventPage.objects.for_actor(api_client_of(request))
        .select_related("vendor", "locale")
        .order_by("-pk")
    )
    window = pages[query.offset : query.offset + query.limit]
    return {"count": pages.count(), "items": [_item(page) for page in window]}


@router.get("/{public_id}", response=EventOut, auth=ApiKey(READ), summary="An event")
def get_event(request: HttpRequest, public_id: uuid.UUID) -> dict[str, Any]:
    return _detail(_event(request, public_id))


@router.post(
    "",
    response={200: ChangeSetOut, 201: ChangeSetOut},
    auth=ApiKey(WRITE),
    summary="Create an event draft",
)
def create_event(
    request: HttpRequest, payload: EventIn, dry_run: DryRun = False
) -> Status[dict[str, Any]]:
    """Create a draft in the primary language. 201 with the ChangeSet; 200 with it on
    `?dry_run=true`. Invalid fields (an invalid `rrule` too) are a 422 problem naming them."""
    client = api_client_of(request)
    changes = services.save_event_draft(
        client,
        _input(request, payload),
        vendor=client.vendor,
        ctx=audit_context(request),
        dry_run=dry_run,
    )
    return Status(200 if dry_run else 201, changeset_out(changes, dry_run=dry_run))


@router.patch(
    "/{public_id}",
    response=ChangeSetOut,
    auth=ApiKey(WRITE),
    summary="Update an event draft",
)
def update_event(
    request: HttpRequest, public_id: uuid.UUID, payload: EventIn, dry_run: DryRun = False
) -> dict[str, Any]:
    """Save a new draft revision from the fields sent; a live page stays as published until the
    revision is submitted."""
    changes = services.save_event_draft(
        api_client_of(request),
        _input(request, payload),
        _event(request, public_id),
        ctx=audit_context(request),
        dry_run=dry_run,
    )
    return changeset_out(changes, dry_run=dry_run)


@router.post(
    "/{public_id}/submit",
    response=ChangeSetOut,
    auth=ApiKey(WRITE),
    summary="Submit the latest revision",
)
def submit_event(
    request: HttpRequest, public_id: uuid.UUID, dry_run: DryRun = False
) -> dict[str, Any]:
    """Send the latest revision to moderation, or publish it when no review applies."""
    changes = services.submit_event(
        api_client_of(request),
        _event(request, public_id),
        ctx=audit_context(request),
        dry_run=dry_run,
    )
    return changeset_out(changes, dry_run=dry_run)


@router.post(
    "/{public_id}/cancel",
    response=ChangeSetOut,
    auth=ApiKey(WRITE),
    summary="Cancel a live event",
)
def cancel_event(
    request: HttpRequest, public_id: uuid.UUID, dry_run: DryRun = False
) -> dict[str, Any]:
    """Withdraw a live event in every language: the page stays online as cancelled."""
    changes = services.cancel_event(
        api_client_of(request),
        _event(request, public_id),
        ctx=audit_context(request),
        dry_run=dry_run,
    )
    return changeset_out(changes, dry_run=dry_run)
