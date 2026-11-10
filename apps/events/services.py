"""Events services: the events index of every language, the status sync (EVT.1), authoring and
moderation (EVT.3, spec 15 "Services").

- `ensure_event_indexes` gives each enabled language's home page its `/events/` index. It runs
  after every `migrate` and whenever a home page is published (the seed, a new language), so
  adding a language to settings needs no step of its own. All indexes share one
  `translation_key`: they are translations of each other, which wagtail-localize needs to place
  a translated event under the index of its language.
- `sync_event_status` writes the denormalised `status` from the Wagtail state.
- `may_edit_events_in_admin` keeps vendors out of event pages in the Wagtail admin (the
  `apps.vendors.backends` backend already keeps them out of the whole admin), and
  `may_create_events_in_admin` lets only platform staff create events there.
- `save_event_draft`, `submit_event`, `cancel_event` (vendor members with the events permission
  and staff), `hide_event`, `restore_event`, `set_vendor_policy` and `set_event_settings` (staff)
  accept `dry_run=True`, return a `ChangeSet` and write one `AuditLog` entry per call, next to
  Wagtail's revisions and log entries. A dry-run runs in a transaction that is rolled back, so
  it writes no revision and no audit entry. Approval and requested changes are Wagtail workflow
  actions: `record_review_decision` audits them from the workflow signals.
- The Wagtail admin's moderation UI (`admin_views`, `wagtail_hooks`) asks `may_moderate_events`,
  `may_review_events`, `admin_edit_refused` and `admin_publish_refused`, and lists
  `events_awaiting_review` and `vendor_event_policies`.
- Moderation: `EventPage.get_workflow()` returns "Event review" (`ensure_event_review_workflow`)
  while the vendor needs review (`engine.needs_review`), and `EventPage.publish` bumps the ICS
  `sequence` (`engine.next_sequence`) on every publish, whoever publishes.
- `resolve_picked_point` checks the point the map picker sends (EVT.13): a "Find coordinates"
  candidate is confirmed against the cached lookup, never trusted from the request.
"""

import datetime as dt
import logging
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, TypedDict

from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser, Group
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F
from django.http import Http404
from django.utils import timezone
from django.utils.translation import gettext as _
from wagtail.actions.unpublish_page import UnpublishPageAction
from wagtail.images import get_image_model
from wagtail.models import (
    Collection,
    Locale,
    Page,
    PageLogEntry,
    Revision,
    Site,
    TaskState,
    Workflow,
    WorkflowState,
)
from wagtail_localize.models import Translation, TranslationSource

from apps.accounts.models import User
from apps.catalog.models import Category, Product
from apps.cms.models import HomePage
from apps.cms.services import sanitise_rich_text_fields
from apps.core.audit import AuditContext, record, user_context
from apps.core.changeset import ChangeSet, to_jsonable
from apps.core.geocoding import AddressQuery, GeocodeResult
from apps.core.geocoding import services as geocoding
from apps.core.i18n import configured_languages
from apps.core.models import AuditLog
from apps.core.services import save_audited
from apps.vendors.engine import VendorAction
from apps.vendors.models import Vendor, VendorMember
from apps.vendors.scoping import is_platform_staff
from apps.vendors.services import ensure_vendor_can_write, has_vendor_permission

from . import engine
from .models import (
    EVENT_MODERATORS,
    EventIndexPage,
    EventPage,
    EventSettings,
    VendorEventPolicy,
    event_review_workflow,
)

INDEX = "events.eventindexpage"
EVENT = "events.eventpage"
INDEX_SLUG = "events"
SYSTEM = AuditContext(actor_label="system:events", source=AuditLog.Source.SYSTEM)

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
    if is_platform_staff(user):
        return True
    return not VendorMember.objects.filter(user_id=user.pk).exists()


def may_create_events_in_admin(user: AbstractBaseUser | AnonymousUser) -> bool:
    """Whether `user` may create (or copy) an event page in the Wagtail admin.

    Only platform staff (`is_staff`, the same definition as the staff-only moderation panel)
    create events there: an event created in the admin without a vendor is a platform event, and
    only platform staff create platform events (spec 15 "Permissions"). Other Wagtail editors
    keep editing existing events under Wagtail's own page permissions.
    """
    return bool(is_platform_staff(user))


# --- The Wagtail admin's moderation UI (EVT.3 part 2/2, spec 15 "Staff admin")


def may_moderate_events(user: AbstractBaseUser | AnonymousUser) -> bool:
    """Whether `user` may hide, restore and cancel events and change the moderation policy and
    settings in the admin: platform staff only (spec 15 "Permissions")."""
    return _is_staff(user)


def may_review_events(user: AbstractBaseUser | AnonymousUser) -> bool:
    """Whether `user` sees the events awaiting "Event review": staff and the "Event moderators"
    group, whose members take the workflow's task."""
    if _is_staff(user):
        return True
    if not (user.is_authenticated and user.is_active):
        return False
    return Group.objects.filter(name=EVENT_MODERATORS, user=user.pk).exists()


def admin_edit_refused(user: AbstractBaseUser | AnonymousUser, page: EventPage) -> bool:
    """A hidden event is edited (and so resubmitted or republished) in the admin by staff only:
    only staff restore it (spec 15 "Hiding")."""
    return page.hidden_at is not None and not _is_staff(user)


def admin_publish_refused(user: AbstractBaseUser | AnonymousUser, page: EventPage) -> bool:
    """A non-staff editor cannot publish an event without review while its vendor needs review:
    the submission goes through "Event review" (`EventPage.get_workflow`)."""
    if _is_staff(user):
        return False
    return page.hidden_at is not None or page.review_needed()


def localize_view_event(kwargs: Mapping[str, Any]) -> EventPage | None:
    """The event behind a wagtail-localize admin view, from its URL arguments (`page_id`,
    `translation_source_id` or `translation_id`); `None` for any other object.

    Those views run no `before_edit_page` hook, so the admin guard resolves their event here.
    """
    source: TranslationSource | None = None
    if "page_id" in kwargs:
        return EventPage.objects.filter(pk=kwargs["page_id"]).first()
    if "translation_source_id" in kwargs:
        source = TranslationSource.objects.filter(pk=kwargs["translation_source_id"]).first()
    elif "translation_id" in kwargs:
        translation = (
            Translation.objects.select_related("source").filter(pk=kwargs["translation_id"]).first()
        )
        source = translation.source if translation is not None else None
    if source is None:
        return None
    # A source names its object by translation key and locale: no match for another model.
    return EventPage.objects.filter(
        translation_key=source.object_id, locale_id=source.locale_id
    ).first()


def admin_localize_refused(
    user: AbstractBaseUser | AnonymousUser, page: EventPage, *, publishes: bool
) -> bool:
    """The rules of `admin_edit_refused` and `admin_publish_refused` for wagtail-localize's
    views: a non-staff user never touches a hidden event's translations, nor publishes them
    without review while the vendor needs review."""
    if admin_edit_refused(user, page):
        return True
    return publishes and admin_publish_refused(user, page)


def events_awaiting_review() -> list[tuple[EventPage, WorkflowState]]:
    """The events in "Event review" now, oldest submission first, with their workflow state."""
    states = (
        WorkflowState.objects.active()
        .filter(
            workflow=event_review_workflow(),
            status=WorkflowState.STATUS_IN_PROGRESS,
            base_content_type=ContentType.objects.get_for_model(Page),
        )
        .select_related("requested_by")
        .order_by("created_at", "pk")
    )
    by_page = {int(state.object_id): state for state in states}
    pages = EventPage.objects.filter(pk__in=list(by_page)).select_related("vendor", "locale")
    found = {page.pk: page for page in pages}
    return [(found[pk], state) for pk, state in by_page.items() if pk in found]


def vendor_event_policies() -> list[VendorEventPolicy]:
    """Every vendor's moderation override, by vendor name (staff list)."""
    return list(
        VendorEventPolicy.objects.select_related("vendor").order_by(
            "vendor__display_name", "vendor_id"
        )
    )


# --- Authoring and moderation (EVT.3, spec 15 "Services")

EDITABLE_FIELDS = (
    "title",
    "slug",
    "summary",
    "event_type",
    "all_day",
    "starts_at",
    "ends_at",
    "start_date",
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
    "geo_source",
    "geo_provider",
    "geo_precision",
    "geo_confidence",
    "geo_at",
)
LINK_FIELDS = ("products", "categories")
INPUT_FIELDS = frozenset({*EDITABLE_FIELDS, "body", *LINK_FIELDS})
TREE_FIELDS = ["path", "depth", "numchild", "url_path"]
# The page-level state an audit entry shows next to the content.
STATE_FIELDS = (
    "status",
    "live",
    "sequence",
    "cancelled_at",
    "hidden_at",
    "hidden_reason",
    "dsa_notice_ref",
)


class EventInput(TypedDict, total=False):
    """What an author sends: any subset of the editable fields (a partial update).

    `body` is StreamField JSON (`[{"type": ..., "value": ...}]`); `products` and `categories`
    are primary keys. The vendor, the language and the page-level state are not input.
    """

    title: str
    slug: str
    summary: str
    event_type: str
    all_day: bool
    starts_at: dt.datetime | None
    ends_at: dt.datetime | None
    start_date: dt.date | None
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
    geo_source: str
    geo_provider: str
    geo_precision: str
    geo_confidence: float | None
    geo_at: dt.datetime | None
    body: list[dict[str, Any]]
    products: list[int]
    categories: list[int]


@dataclass
class EventChangeSet(ChangeSet):
    """The changes of one event service call, with the page it acted on (None after a dry-run
    create: nothing was saved)."""

    page: EventPage | None = None


class EventRefused(PermissionDenied):
    """The event's state refuses the action to this actor: a hidden page for its vendor."""


def _is_staff(user: Any) -> bool:
    return bool(is_platform_staff(user))


def _require_staff(user: AbstractBaseUser | AnonymousUser) -> None:
    if not _is_staff(user):
        raise PermissionDenied("only platform staff moderate events")


def _context(actor: AbstractBaseUser | AnonymousUser, ctx: AuditContext | None) -> AuditContext:
    return ctx if ctx is not None else user_context(actor, ip=None)


def _user(actor: Any) -> User | None:
    """The Django user behind `actor`: none for an API key (a credential, CORE.13), whose
    revisions and workflow steps carry no user; the audit entry names the key."""
    return actor if isinstance(actor, User) else None


def _may_author(actor: Any, vendor: Vendor | None) -> bool:
    if _is_staff(actor):
        return True
    if vendor is None:
        return False
    if not isinstance(actor, User):
        # A vendor-bound API key acts for its whole vendor (CORE.13); its scope, checked by the
        # API, replaces the member permission. A platform key never authors.
        credential_vendor = getattr(actor, "credential_vendor_id", None)
        return credential_vendor is not None and credential_vendor == vendor.pk
    return has_vendor_permission(actor, vendor, VendorAction.EVENTS)


def _event_for(actor: Any, page: EventPage) -> EventPage:
    """The stored page, when `actor` may act on it: staff on every event, a vendor member with
    the events permission on its vendor's events (CORE.3 scoping: another vendor's is a 404)."""
    found: EventPage | None = EventPage.objects.for_actor(actor).filter(pk=page.pk).first()
    if found is None:
        raise Http404("No event matches the given query.")
    if not _may_author(actor, found.vendor):
        raise PermissionDenied("no permission to manage this vendor's events")
    return found


def _refuse_hidden(actor: Any, page: EventPage) -> None:
    if page.hidden_at is not None and not _is_staff(actor):
        raise EventRefused("a hidden event is restored by platform staff only")


def _in_review(page: EventPage) -> bool:
    state = page.current_workflow_state
    return state is not None and state.status == WorkflowState.STATUS_IN_PROGRESS


def _fresh(page: EventPage) -> EventPage:
    fresh: EventPage = EventPage.objects.get(pk=page.pk)
    return fresh


def _translations(page: EventPage) -> list[EventPage]:
    """Every page of the event (all languages): cancel, hide and restore act on them together."""
    return list(EventPage.objects.filter(translation_key=page.translation_key).order_by("pk"))


def _key(page: EventPage) -> str:
    return str(page.public_id)


def _vendor_key(page: EventPage) -> str | None:
    vendor = page.vendor
    return str(vendor.public_id) if vendor is not None else None


def _state(page: EventPage) -> dict[str, Any]:
    state = {name: getattr(page, name) for name in STATE_FIELDS}
    snapshot: dict[str, Any] = to_jsonable({"vendor": _vendor_key(page), **state})
    return snapshot


def _snapshot(page: EventPage) -> dict[str, Any]:
    """The content of a draft, for the ChangeSet and the audit entry."""
    data: dict[str, Any] = {name: getattr(page, name) for name in EDITABLE_FIELDS}
    data["body"] = page.body.get_prep_value()
    data["products"] = sorted(product.pk for product in page.products.all())
    data["categories"] = sorted(category.pk for category in page.categories.all())
    data["vendor"] = _vendor_key(page)
    data["locale"] = page.locale.language_code
    return dict(to_jsonable(data))


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _strings(item)


def _set_body(page: EventPage, raw: Any, *, staff: bool) -> None:
    """Validate the StreamField JSON with each block's own `clean()` and set it.

    Vendors get the vendor-safe blocks only, and no `javascript:` or `data:` links.
    """
    if not isinstance(raw, list) or not all(isinstance(item, Mapping) for item in raw):
        raise ValidationError({"body": _("Send the body as a list of blocks.")})
    types = [str(item.get("type", "")) for item in raw]
    refused = engine.refused_blocks(types, is_staff=staff)
    if refused:
        raise ValidationError(
            {"body": _("These blocks are not available: %s.") % ", ".join(sorted(set(refused)))}
        )
    if not staff:
        _check_vendor_body(page, raw)
    field = EventPage._meta.get_field("body")
    unknown = sorted(set(types) - set(field.stream_block.child_blocks))
    if unknown:
        raise ValidationError({"body": _("Unknown blocks: %s.") % ", ".join(unknown)})
    value = field.to_python(raw)
    try:
        page.body = field.stream_block.clean(value)
    except ValidationError as exc:
        raise ValidationError({"body": exc.messages}) from exc


def _vendor_images_allowed(vendor: Vendor, ids: list[Any]) -> bool:
    """Whether every id is an image in `vendor`'s own collection (or one below it)."""
    if not all(isinstance(pk, int) and not isinstance(pk, bool) for pk in ids):
        return False
    wanted = set(ids)
    own = Collection.objects.filter(
        depth=2, name=engine.vendor_collection_name(str(vendor.public_id))
    ).first()
    if own is None:
        return not wanted
    allowed = Collection.objects.descendant_of(own, inclusive=True)
    found = get_image_model().objects.filter(pk__in=wanted, collection__in=allowed)
    return bool(found.count() == len(wanted))


def _check_vendor_body(page: EventPage, raw: list[Mapping[str, Any]]) -> None:
    """The vendor-only body rules (spec 15 "Vendor-safe block subset"), for every entry point:
    size limits first (they bound every later walk), unsafe links, no embeds by id, own images
    only, `https://` or own-event CTA links."""
    problem = engine.body_limit_problem(raw)
    if problem is not None:
        raise ValidationError({"body": problem})
    if any(engine.has_unsafe_link(text) for text in _strings(raw)):
        raise ValidationError({"body": _("Links must not use javascript: or data: addresses.")})
    if any(engine.has_foreign_reference(text) for text in _strings(raw)):
        raise ValidationError({"body": _("Rich text cannot embed media or link documents.")})
    vendor = page.vendor
    ids = engine.image_ids(raw)
    if ids and (vendor is None or not _vendor_images_allowed(vendor, ids)):
        raise ValidationError({"body": _("Choose images from your own image library.")})
    own_pages: set[int] = set()
    for link in engine.cta_links(raw):
        url, target = link.get("url"), link.get("page")
        if url:
            url_problem = engine.cta_url_problem(str(url))
            if url_problem is not None:
                raise ValidationError({"body": url_problem})
        if target not in (None, ""):
            if isinstance(target, bool) or not isinstance(target, int):
                raise ValidationError({"body": _("Choose one of your own events.")})
            if not own_pages and vendor is not None:
                own_pages = set(
                    EventPage.objects.filter(vendor=vendor).values_list("pk", flat=True)
                )
            if target not in own_pages:
                raise ValidationError({"body": _("Choose one of your own events.")})


def _set_links(page: EventPage, data: Mapping[str, Any]) -> None:
    """Products and categories; a vendor event links only to its own vendor's products."""
    if "products" in data:
        ids = set(data["products"] or [])
        products = list(Product.objects.filter(pk__in=ids))
        if len(products) != len(ids):
            raise ValidationError({"products": _("Choose existing products.")})
        if page.vendor_id is not None and any(p.vendor_id != page.vendor_id for p in products):
            raise ValidationError({"products": _("An event links only its vendor's products.")})
        page.products = products
    if "categories" in data:
        ids = set(data["categories"] or [])
        categories = list(Category.objects.filter(pk__in=ids))
        if len(categories) != len(ids):
            raise ValidationError({"categories": _("Choose existing categories.")})
        page.categories = categories


def _apply(page: EventPage, data: Mapping[str, Any], *, staff: bool) -> None:
    unknown = sorted(set(data) - INPUT_FIELDS)
    if unknown:
        raise ValidationError(dict.fromkeys(unknown, _("This field cannot be set.")))
    slug = data.get("slug")
    if slug is not None and slug in engine.RESERVED_SLUGS:
        raise ValidationError({"slug": _("This address is reserved for the events index.")})
    for name in ("exdates", "exdate_days"):
        if len(data.get(name) or ()) > engine.MAX_EXDATES:
            raise ValidationError(
                {name: _("At most %(max)d exclusions.") % {"max": engine.MAX_EXDATES}}
            )
    for name in EDITABLE_FIELDS:
        if name in data:
            setattr(page, name, data[name])
    if "body" in data:
        _set_body(page, data["body"], staff=staff)
        # A revision is a JSON snapshot: `save_revision` skips `pre_save`, so a draft edit would
        # keep the raw rich text. The one sanitiser (CMS.12) runs here, for create and edit.
        sanitise_rich_text_fields(page, only={"body"})
    _set_links(page, data)


def _index_for(locale: Locale) -> EventIndexPage:
    index: EventIndexPage | None = EventIndexPage.objects.filter(locale=locale).first()
    if index is None:
        ensure_event_indexes()
        index = EventIndexPage.objects.filter(locale=locale).first()
    if index is None:
        raise ValidationError({"locale": _("Events cannot be written in this language.")})
    return index


def _record(ctx: AuditContext, action: str, page: EventPage, before: Any, after: Any) -> None:
    """The one audit entry of a service call (spec 15 "Services")."""
    record(ctx, action, EVENT, _key(page), before, after)


@contextmanager
def _mutation(dry_run: bool, *, actor: Any = None, vendor: Vendor | None = None) -> Iterator[None]:
    """One transaction per service call; a dry-run rolls it back, revisions and audit included.

    A vendor's own write (`actor` not staff, `vendor` set) first re-reads the vendor's status
    inside the transaction (`ensure_vendor_can_write`, CORE.14, the CAT.3 pattern): a suspension
    between authentication and the write raises `engine.VendorCannotWrite` (vendors app).
    """
    with transaction.atomic():
        if vendor is not None and not _is_staff(actor):
            ensure_vendor_can_write(vendor)
        yield
        if dry_run:
            transaction.set_rollback(True)


def save_event_draft(
    actor: Any,
    data: EventInput | Mapping[str, Any],
    page: EventPage | None = None,
    *,
    vendor: Vendor | None = None,
    locale: Locale | None = None,
    ctx: AuditContext | None = None,
    dry_run: bool = False,
) -> EventChangeSet:
    """Create an event draft, or save a new revision of `page`, as `actor`.

    A new page goes under the events index of `locale` (default: the primary language), with
    `original_locale` set to it and a slug from the title. An edit applies `data` (a partial
    update) over the latest revision and saves a revision: a live page stays as published until
    the revision is published (`submit_event`). Every change is a Wagtail revision in the
    actor's name, with one audit entry.

    Vendor members with the events permission write their vendor's events; only platform staff
    write platform events (`vendor=None`) and other vendors' events. A hidden page is refused
    to its vendor (`EventRefused`), and so is a page awaiting review (`ValidationError`): the
    approval publishes the reviewed revision. Invalid data raises `ValidationError` with field
    errors.
    """
    ctx = _context(actor, ctx)
    staff = _is_staff(actor)
    if page is None:
        return _create_event(actor, data, vendor, locale, staff=staff, ctx=ctx, dry_run=dry_run)
    stored = _event_for(actor, page)
    _refuse_hidden(actor, stored)
    if not staff and _in_review(stored):
        # The approval publishes what the moderator reviewed: no vendor edit while it waits.
        raise ValidationError(_("The event is awaiting review and cannot be edited."))
    changes = EventChangeSet(page=stored)
    with _mutation(dry_run, actor=actor, vendor=stored.vendor):
        draft: EventPage = stored.get_latest_revision_as_object()
        before = _snapshot(draft)
        _apply(draft, data, staff=staff)
        draft.full_clean()
        draft.save_revision(user=_user(actor), log_action=True)
        after = _snapshot(draft)
        changes.update(EVENT, _key(stored), before, after)
        _record(ctx, "events.event_draft_saved", stored, before, after)
    return changes


def _create_event(
    actor: Any,
    data: Mapping[str, Any],
    vendor: Vendor | None,
    locale: Locale | None,
    *,
    staff: bool,
    ctx: AuditContext,
    dry_run: bool,
) -> EventChangeSet:
    if vendor is None and not staff:
        raise PermissionDenied("only platform staff create platform events")
    if not _may_author(actor, vendor):
        raise PermissionDenied("no permission to manage this vendor's events")
    locale = locale or Locale.get_default()
    changes = EventChangeSet()
    with _mutation(dry_run, actor=actor, vendor=vendor):
        index = _index_for(locale)
        page = EventPage(
            vendor=vendor, locale=locale, original_locale=locale, owner=_user(actor), live=False
        )
        _apply(page, data, staff=staff)
        if not data.get("slug"):
            taken = set(Page.objects.child_of(index).values_list("slug", flat=True))
            page.slug = engine.event_slug(page.title or "", taken=taken)
        # Field errors before the insert; the tree fields come from `add_child`.
        page.full_clean(exclude=TREE_FIELDS)
        index.add_child(instance=page)
        page.save_revision(user=_user(actor), log_action=True)
        after = _snapshot(page)
        changes.create(EVENT, _key(page), after)
        _record(ctx, "events.event_created", page, None, after)
        changes.page = None if dry_run else page
    return changes


def submit_event(
    actor: Any,
    page: EventPage,
    *,
    ctx: AuditContext | None = None,
    dry_run: bool = False,
) -> EventChangeSet:
    """Submit the latest revision: into "Event review" when `get_workflow()` applies, else publish.

    A submission sent back with requested changes resumes its review. Staff submissions never
    go through review. A page already awaiting review is refused (`ValidationError`), and so is
    a hidden page for its vendor (`EventRefused`).
    """
    ctx = _context(actor, ctx)
    staff = _is_staff(actor)
    stored = _event_for(actor, page)
    _refuse_hidden(actor, stored)
    changes = EventChangeSet(page=stored)
    with _mutation(dry_run, actor=actor, vendor=stored.vendor):
        before = _state(stored)
        state = stored.current_workflow_state
        if _in_review(stored):
            raise ValidationError(_("The event is already awaiting review."))
        if not staff:
            # The collection rule can change after a draft was saved: check what is published.
            latest: EventPage = stored.get_latest_revision_as_object()
            _check_vendor_body(latest, latest.body.get_prep_value())
        revision = stored.get_latest_revision() or stored.save_revision(user=_user(actor))
        workflow = None if staff else stored.get_workflow()
        if workflow is not None and state is not None:
            state.resume(user=_user(actor))
            outcome = "resubmitted"
        elif workflow is not None:
            workflow.start(stored, _user(actor))
            outcome = "submitted"
        else:
            if state is not None:
                state.cancel(user=_user(actor))
            revision.publish(user=_user(actor), skip_permission_checks=True)
            outcome = "published"
        sync_event_status(stored)
        after = _state(_fresh(stored)) | {"submission": outcome}
        changes.update(EVENT, _key(stored), before, after)
        _record(ctx, f"events.event_{outcome}", stored, before, after)
    return changes


def hide_event(
    staff: Any,
    page: EventPage,
    reason: str,
    dsa_notice_ref: str = "",
    *,
    ctx: AuditContext | None = None,
    dry_run: bool = False,
) -> EventChangeSet:
    """Hide an event in every language: unpublish it, record who, when and why (spec 15).

    Staff only, with a reason; `dsa_notice_ref` names the CMP.5 notice when there is one. A
    review in progress is cancelled, so no approval publishes a hidden page. Only staff restore
    it (`restore_event`).
    """
    _require_staff(staff)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": _("Give the reason for hiding the event.")})
    ctx = _context(staff, ctx)
    stored = _event_for(staff, page)
    if stored.hidden_at is not None:
        raise ValidationError(_("The event is already hidden."))
    changes = EventChangeSet(page=stored)
    with _mutation(dry_run):
        before = _state(stored)
        pages = _translations(stored)
        EventPage.objects.filter(pk__in=[p.pk for p in pages]).update(
            hidden_at=timezone.now(),
            hidden_by=staff,
            hidden_reason=reason,
            dsa_notice_ref=dsa_notice_ref or "",
        )
        for translation in pages:
            current = _fresh(translation)
            state = current.current_workflow_state
            if state is not None:
                state.cancel(user=staff)
            if current.live:
                UnpublishPageAction(current, user=staff).execute(skip_permission_checks=True)
            sync_event_status(current)
        after = _state(_fresh(stored)) | {"languages": len(pages)}
        changes.update(EVENT, _key(stored), before, after)
        _record(ctx, "events.event_hidden", stored, before, after)
    return changes


def _last_published_revision(page: EventPage) -> Revision | None:
    entry = (
        PageLogEntry.objects.filter(page=page, action="wagtail.publish", revision__isnull=False)
        .select_related("revision")
        .order_by("-timestamp", "-pk")
        .first()
    )
    return entry.revision if entry is not None else None


def restore_event(
    staff: Any,
    page: EventPage,
    *,
    ctx: AuditContext | None = None,
    dry_run: bool = False,
) -> EventChangeSet:
    """Restore a hidden event in every language: republish its latest approved revision.

    Staff only. The event comes back to calendars, so `sequence` goes up by one.
    """
    _require_staff(staff)
    ctx = _context(staff, ctx)
    stored = _event_for(staff, page)
    if stored.hidden_at is None:
        raise ValidationError(_("The event is not hidden."))
    changes = EventChangeSet(page=stored)
    with _mutation(dry_run):
        before = _state(stored)
        pages = _translations(stored)
        EventPage.objects.filter(pk__in=[p.pk for p in pages]).update(
            hidden_at=None,
            hidden_by=None,
            hidden_reason="",
            dsa_notice_ref="",
            sequence=F("sequence") + 1,
            content_changed_at=timezone.now(),
        )
        for translation in pages:
            current = _fresh(translation)
            revision = _last_published_revision(current)
            if revision is not None:
                revision.publish(user=staff, skip_permission_checks=True)
            sync_event_status(current)
        after = _state(_fresh(stored)) | {"languages": len(pages)}
        changes.update(EVENT, _key(stored), before, after)
        _record(ctx, "events.event_restored", stored, before, after)
    return changes


def cancel_event(
    actor: Any,
    page: EventPage,
    *,
    ctx: AuditContext | None = None,
    dry_run: bool = False,
) -> EventChangeSet:
    """Cancel a live event in every language, without review (it only withdraws information).

    Copies each live revision, sets `cancelled_at` and publishes it: the page stays online with
    a "Cancelled" message and `sequence` goes up (the publish rule). A pending edit in review is
    cancelled with it. Vendor members with the events permission and staff; a hidden page is
    refused to its vendor.
    """
    ctx = _context(actor, ctx)
    stored = _event_for(actor, page)
    _refuse_hidden(actor, stored)
    if not stored.live or stored.live_revision_id is None:
        raise ValidationError(_("Only a published event can be cancelled."))
    if stored.cancelled_at is not None:
        raise ValidationError(_("The event is already cancelled."))
    changes = EventChangeSet(page=stored)
    with _mutation(dry_run, actor=actor, vendor=stored.vendor):
        before = _state(stored)
        now = timezone.now()
        pages = _translations(stored)
        for translation in pages:
            current = _fresh(translation)
            state = current.current_workflow_state
            if state is not None:
                state.cancel(user=_user(actor))
            if not current.live or current.live_revision is None:
                continue
            cancelled: EventPage = current.live_revision.as_object()
            cancelled.cancelled_at = now
            cancelled.save_revision(user=_user(actor), log_action=True).publish(
                user=_user(actor), skip_permission_checks=True
            )
        after = _state(_fresh(stored)) | {"languages": len(pages)}
        changes.update(EVENT, _key(stored), before, after)
        _record(ctx, "events.event_cancelled", stored, before, after)
    return changes


def record_review_decision(
    state: WorkflowState,
    user: Any,
    *,
    approved: bool,
    ctx: AuditContext | None = None,
    dry_run: bool = False,
) -> EventChangeSet:
    """Audit a moderator's decision on "Event review" (Wagtail's workflow signals) and sync
    the page's status. Pages of other types are left alone (an empty ChangeSet)."""
    changes = EventChangeSet()
    page = state.content_object
    if isinstance(page, Page):
        page = page.specific
    if not isinstance(page, EventPage):
        return changes
    task_state = (
        TaskState.objects.filter(workflow_state=state).order_by("-finished_at", "-pk").first()
    )
    with _mutation(dry_run):
        sync_event_status(page)
        current = _fresh(page)
        before = {"vendor": _vendor_key(current), "workflow": WorkflowState.STATUS_IN_PROGRESS}
        after = _state(current) | {
            "workflow": state.status,
            "comment": task_state.comment if task_state is not None else "",
        }
        changes.page = current
        changes.update(EVENT, _key(current), before, after)
        if ctx is None:
            ctx = user_context(user, ip=None) if user is not None else SYSTEM
        action = "events.event_approved" if approved else "events.event_changes_requested"
        _record(ctx, action, current, before, after)
    return changes


# --- Moderation policy and settings (staff, audited)


def _policy_snapshot(policy: VendorEventPolicy) -> dict[str, Any]:
    return {"vendor": str(policy.vendor.public_id), "mode": policy.mode}


def set_vendor_policy(
    staff: Any,
    vendor: Vendor,
    mode: str,
    *,
    ctx: AuditContext | None = None,
    dry_run: bool = False,
) -> ChangeSet:
    """Set a vendor's moderation override (`always_review`, `trusted` or `default`)."""
    _require_staff(staff)
    if mode not in VendorEventPolicy.Mode.values:
        raise ValidationError({"mode": _("Choose a moderation mode.")})
    policy = VendorEventPolicy.objects.filter(vendor=vendor).first() or VendorEventPolicy(
        vendor=vendor
    )
    policy.mode = mode
    return save_audited(
        policy,
        _policy_snapshot,
        key=str(vendor.public_id),
        ctx=_context(staff, ctx),
        created="events.policy_set",
        updated="events.policy_changed",
        dry_run=dry_run,
    )


def _settings_snapshot(settings: EventSettings) -> dict[str, Any]:
    return {
        "site": settings.site_id,
        "moderation_threshold": settings.moderation_threshold,
        "cancelled_retention_days": settings.cancelled_retention_days,
    }


def set_event_settings(
    staff: Any,
    site: Site,
    *,
    moderation_threshold: int | None = None,
    cancelled_retention_days: int | None = None,
    ctx: AuditContext | None = None,
    dry_run: bool = False,
) -> ChangeSet:
    """Change the events settings of `site` (the moderation threshold, the retention)."""
    _require_staff(staff)
    settings = EventSettings.for_site(site)
    if moderation_threshold is not None:
        settings.moderation_threshold = moderation_threshold
    if cancelled_retention_days is not None:
        settings.cancelled_retention_days = cancelled_retention_days
    settings.full_clean()
    return save_audited(
        settings,
        _settings_snapshot,
        key=str(site.pk),
        ctx=_context(staff, ctx),
        created="events.settings_set",
        updated="events.settings_changed",
        dry_run=dry_run,
    )


def ensure_event_review_workflow() -> Workflow:
    """The "Event review" workflow, created when missing (after `migrate` and on first use)."""
    return event_review_workflow()


# --- The map picker (EVT.13, spec 15 "Map picker" and "Confirmation")


@dataclass(frozen=True)
class PickedPoint:
    """What to store for the point the map picker sent: its source and, for a newly confirmed
    "Find coordinates" candidate (as chosen, or moved after choosing it), that candidate (its
    metadata comes from the cache). With a source other than "geocoded" the sent point is kept
    and only the candidate's metadata is stored."""

    source: str
    candidate: GeocodeResult | None = None


def resolve_picked_point(
    page: EventPage,
    *,
    latitude: Decimal | None,
    longitude: Decimal | None,
    source: str,
    address: AddressQuery,
    chosen: tuple[Decimal, Decimal] | None = None,
    now: dt.datetime | None = None,
) -> PickedPoint:
    """Check the point the picker (or a person typing) sent for `page`, before it is applied.

    - No point: no source (the form then clears the geocoding metadata).
    - A point placed on the map or typed keeps its source; a point without one was typed (the
      inputs are the keyboard and no-JavaScript alternative).
    - A "Find coordinates" candidate the author chose and then moved (`chosen`, the candidate's
      own point): the moved point keeps its source and gets the candidate's metadata when a
      cached lookup of the form's address has a candidate there (spec 15 "Confirmation": the
      author may adjust the point before confirming). A `chosen` point no lookup returned adds
      no metadata. The same rule as moving the geocoded point of a saved page.
    - A "geocoded" point must be a candidate of a cached lookup of the form's address: the
      request never sets a provider, a precision or a confidence. An unchanged geocoded point
      of the page stays as it is, even after its cache entry expired.

    Raises `ValidationError` on `geo_source` for a geocoded point that no lookup returned. Half a
    point is left to the model's validation ("Give both coordinates").
    """
    if latitude is None and longitude is None:
        return PickedPoint(EventPage.GeoSource.NONE)
    if source != EventPage.GeoSource.GEOCODED:
        moved_from = None
        if chosen is not None and latitude is not None and longitude is not None:
            moved_from = geocoding.cached_candidate(address, chosen[0], chosen[1], now=now)
        return PickedPoint(source or EventPage.GeoSource.MANUAL, moved_from)
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
