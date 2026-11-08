"""Events services (EVT.1): the events index of every language and the status sync.

The authoring, moderation, hiding and cancelling services (`save_event_draft`, `submit_event`,
`hide_event` ...) come with EVT.3, with their `AuditLog` entries.

- `ensure_event_indexes` gives each enabled language's home page its `/events/` index. It runs
  after every `migrate` and whenever a home page is published (the seed, a new language), so
  adding a language to settings needs no step of its own. All indexes share one
  `translation_key`: they are translations of each other, which wagtail-localize needs to place
  a translated event under the index of its language.
- `sync_event_status` writes the denormalised `status` from the Wagtail state.
- `may_edit_events_in_admin` keeps vendors out of event pages in the Wagtail admin (the
  `apps.vendors.backends` backend already keeps them out of the whole admin), and
  `may_create_events_in_admin` lets only platform staff create events there.
- `resolve_picked_point` checks the point the map picker sends (EVT.13): a "Find coordinates"
  candidate is confirmed against the cached lookup, never trusted from the request.
"""

import datetime as dt
import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal

from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from wagtail.models import Page

from apps.cms.models import HomePage
from apps.core.changeset import ChangeSet
from apps.core.geocoding import AddressQuery, GeocodeResult
from apps.core.geocoding import services as geocoding
from apps.core.i18n import configured_languages
from apps.vendors.models import VendorMember

from . import engine
from .models import EventIndexPage, EventPage

INDEX = "events.eventindexpage"
EVENT = "events.eventpage"
INDEX_SLUG = "events"

logger = logging.getLogger(__name__)

# Titles of the index page per language; a language without an entry gets the primary one's.
INDEX_TITLES = {
    "en": "Events",
    "it": "Eventi",
    "de": "Veranstaltungen",
    "fr": "Événements",
    "es": "Eventos",
}


def _index_title(language: str) -> str:
    return INDEX_TITLES.get(language) or INDEX_TITLES["en"]


def ensure_event_indexes(*, dry_run: bool = False) -> ChangeSet:
    """Add a published `EventIndexPage` under every enabled language's home page lacking one.

    Idempotent: existing indexes are never changed. New ones take the `translation_key` of an
    existing index, so all languages' indexes are translations of each other. A home page that
    already has another page with the slug `events` is skipped with a warning: the editors
    decide, and `post_migrate` never fails on it.
    """
    changes = ChangeSet()
    languages = configured_languages()
    with transaction.atomic():
        indexes = EventIndexPage.objects.all()
        existing = indexes.order_by("path").first()
        key = existing.translation_key if existing is not None else uuid.uuid4()
        homes = (
            HomePage.objects.filter(locale__language_code__in=languages)
            .select_related("locale")
            .order_by("path")
        )
        for home in homes:
            if indexes.child_of(home).exists():
                continue
            if Page.objects.child_of(home).filter(slug=INDEX_SLUG).exists():
                logger.warning(
                    "No events index under home page %s: the slug %r is taken", home.pk, INDEX_SLUG
                )
                continue
            language = home.locale.language_code
            index = EventIndexPage(
                title=_index_title(language),
                slug=INDEX_SLUG,
                locale=home.locale,
                translation_key=key,
            )
            home.add_child(instance=index)
            index.save_revision().publish()
            changes.create(INDEX, f"{key}:{language}", {"title": index.title, "slug": INDEX_SLUG})
        if dry_run:
            transaction.set_rollback(True)
    return changes


def sync_event_status(page: EventPage, changes: ChangeSet | None = None) -> str:
    """Write `page.status` from `live`, the workflow state, `cancelled_at` and `hidden_at`.

    Reads the stored row (a caller's copy may predate a publish) and records the change in the
    caller's `ChangeSet` when given. Returns the status.
    """
    current: EventPage = EventPage.objects.get(pk=page.pk)
    status = engine.event_status(
        live=current.live,
        in_workflow=current.workflow_in_progress,
        cancelled=current.cancelled_at is not None,
        hidden=current.hidden_at is not None,
    )
    if status != current.status:
        EventPage.objects.filter(pk=page.pk).update(status=status)
        if changes is not None:
            changes.update(EVENT, str(page.pk), {"status": current.status}, {"status": status})
    page.status = status
    return status


def may_edit_events_in_admin(user: AbstractBaseUser | AnonymousUser) -> bool:
    """Whether `user` may create, edit or delete event pages in the Wagtail admin.

    Vendors author events in the vendor area, never in the Wagtail admin (spec 15 "How vendors
    author an `EventPage`"): its explorer, image library and reports are not tenant-scoped. A
    vendor member who is not platform staff is refused even when an editor group gave them
    Wagtail access. Staff and other editors keep Wagtail's own page permissions.
    """
    if not user.is_authenticated:
        return False
    if getattr(user, "is_staff", False):
        return True
    return not VendorMember.objects.filter(user_id=user.pk).exists()


def may_create_events_in_admin(user: AbstractBaseUser | AnonymousUser) -> bool:
    """Whether `user` may create (or copy) an event page in the Wagtail admin.

    Only platform staff (`is_staff`, the same definition as the staff-only moderation panel)
    create events there: an event created in the admin without a vendor is a platform event, and
    only platform staff create platform events (spec 15 "Permissions"). Other Wagtail editors
    keep editing existing events under Wagtail's own page permissions.
    """
    return bool(user.is_authenticated and user.is_active and getattr(user, "is_staff", False))


# --- The map picker (EVT.13, spec 15 "Map picker" and "Confirmation")


@dataclass(frozen=True)
class PickedPoint:
    """What to store for the point the map picker sent: its source and, for a newly confirmed
    "Find coordinates" candidate, that candidate (its metadata comes from the cache)."""

    source: str
    candidate: GeocodeResult | None = None


def resolve_picked_point(
    page: EventPage,
    *,
    latitude: Decimal | None,
    longitude: Decimal | None,
    source: str,
    address: AddressQuery,
    now: dt.datetime | None = None,
) -> PickedPoint:
    """Check the point the picker (or a person typing) sent for `page`, before it is applied.

    - No point: no source (the form then clears the geocoding metadata).
    - A point placed on the map or typed keeps its source; a point without one was typed (the
      inputs are the keyboard and no-JavaScript alternative).
    - A "geocoded" point must be a candidate of a cached lookup of the form's address: the
      request never sets a provider, a precision or a confidence. An unchanged geocoded point
      of the page stays as it is, even after its cache entry expired.

    Raises `ValidationError` on `geo_source` for a geocoded point that no lookup returned. Half a
    point is left to the model's validation ("Give both coordinates").
    """
    if latitude is None and longitude is None:
        return PickedPoint(EventPage.GeoSource.NONE)
    if source != EventPage.GeoSource.GEOCODED:
        return PickedPoint(source or EventPage.GeoSource.MANUAL)
    if latitude is None or longitude is None:
        return PickedPoint(source)
    unchanged = page.geo_source == EventPage.GeoSource.GEOCODED and (
        page.latitude,
        page.longitude,
    ) == (latitude, longitude)
    if unchanged:
        return PickedPoint(source)
    candidate = geocoding.cached_candidate(address, latitude, longitude, now=now)
    if candidate is None:
        raise ValidationError(
            {
                "geo_source": _(
                    "These coordinates are not a “Find coordinates” result for this address: "
                    "choose “Placed on the map” or “Typed by hand”, or find the coordinates again."
                )
            }
        )
    return PickedPoint(source, candidate)
