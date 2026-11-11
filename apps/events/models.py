"""Event pages: promotions, trade fairs and meetings (EVT.1, spec 15 "Page models").

An `EventIndexPage` (`/<lang>/events/`) sits under each language's `HomePage`, one per enabled
locale, all linked as translations (`services.ensure_event_indexes`). Its children are
`EventPage`s, written in any enabled language (owner decision Q-72, an exception to D-18):
`original_locale` records it, and translations are optional. The structured fields (dates,
recurrence, location, coordinates, vendor, cancellation, hiding) are synchronised to every
translation by wagtail-localize and cannot be overridden; title, summary, body and venue name are
translated. The product and category links are many-to-many fields, which wagtail-localize does
not synchronise: the EVT.8 publish service copies them to every translation. `status` is not
synchronised: each translation's comes from its own Wagtail state.

A vendor's closure is not an event (owner decision Q-73): there is no closure type.

Tenancy (CORE.3): `EventPage.objects` is Wagtail's page manager with the vendor scoping of
`apps.vendors.scoping`, so `EventPage.objects.for_actor(user)` holds every event for staff, the
member's own vendor's events for a vendor member, and nothing for anyone else. A platform event
(no vendor) is visible to staff only.

`status` is denormalised for filters (vendor area, API, admin): `services.sync_event_status`
keeps it in step with `live`, the workflow state, `cancelled_at` and `hidden_at`.

Moderation (EVT.3): `EventPage.get_workflow()` returns the "Event review" workflow
(`event_review_workflow`) while the vendor needs review (`engine.needs_review`: policy,
threshold, approved events), and none otherwise, so Wagtail publishes at once. Every publish goes
through `EventPage.publish`, which bumps `sequence` when the revision changes a field the ICS
shows. The page-level state (`ROW_FIELDS`: hiding, sequence, status) lives on the page row and
never comes back from a revision.

Validation lives in `clean()` (field errors for the forms) and in check constraints (the same
rules, for every other write path). The admin panels are in `panels` and the admin form in
`forms` (EVT.1 part 2/2); the public templates are `events/event_index_page.html` and
`events/event_page.html`.
"""

import datetime as dt
import uuid
import zoneinfo
from decimal import Decimal
from typing import Any, ClassVar

from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django.db.models import F, Q
from django.http import Http404, HttpRequest
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from modelcluster.fields import ParentalManyToManyField
from wagtail import blocks
from wagtail.admin.panels import FieldPanel
from wagtail.contrib.settings.models import BaseSiteSetting
from wagtail.fields import RichTextField, StreamField
from wagtail.images import get_image_model_string
from wagtail.models import (
    GroupApprovalTask,
    Page,
    PageManager,
    PageQuerySet,
    Site,
    Workflow,
    WorkflowState,
    WorkflowTask,
)
from wagtail_localize.fields import SynchronizedField

from apps.cms import roles
from apps.cms.blocks import (
    RICH_TEXT_FEATURES,
    CallToActionBlock,
    FaqBlock,
    FigureBlock,
    GalleryBlock,
    LaunchCountdownBlock,
    paragraph_block,
)
from apps.cms.models import CountdownCacheMixin
from apps.core.fields import CountryField
from apps.core.geocoding import GeocodeResult
from apps.core.models import BaseModel
from apps.vendors.models import Vendor
from apps.vendors.scoping import VendorScopedManager, VendorScopedQuerySet

from . import engine
from .forms import EventPageForm
from .panels import event_content_panels

# --- Choices


class EventType(models.TextChoices):
    PROMOTION = engine.PROMOTION, _("Promotion")
    FAIR = engine.FAIR, _("Trade fair or meeting")
    OTHER = engine.OTHER, _("Other")


class Attendance(models.TextChoices):
    IN_PERSON = "in_person", _("In person")
    ONLINE = "online", _("Online")
    MIXED = "mixed", _("In person and online")


class GeoSource(models.TextChoices):
    NONE = "", _("No coordinates")
    GEOCODED = "geocoded", _("Found from the address")
    MAP = "map", _("Placed on the map")
    MANUAL = "manual", _("Typed by hand")


class EventStatus(models.TextChoices):
    DRAFT = engine.DRAFT, _("Draft")
    PENDING = engine.PENDING, _("Awaiting review")
    PUBLISHED = engine.PUBLISHED, _("Published")
    CANCELLED = engine.CANCELLED, _("Cancelled")
    HIDDEN = engine.HIDDEN, _("Hidden")


def default_timezone() -> str:
    """The platform's local time zone (spec 01 "Time")."""
    return str(settings.BUSINESS_TIME_ZONE)


def event_blocks() -> list[tuple[str, blocks.Block]]:
    """The body blocks of an event, from the CMS.2 library (spec 15 "Body").

    The CMS.9 launch countdown is offered (spec 10 §10.2; `EventPage` caps its cache at the
    target). The CMS.10 product grid joins when that task adds it; embeds only with the SEO.5
    consent rules. The EVT.3 services give vendors `engine.VENDOR_SAFE_BLOCKS` only.
    """
    return [
        ("paragraph", paragraph_block()),
        ("figure", FigureBlock()),
        ("gallery", GalleryBlock()),
        ("cta", CallToActionBlock()),
        ("faq", FaqBlock()),
        ("countdown", LaunchCountdownBlock()),
    ]


# --- Pages


class EventIndexPage(Page):  # type: ignore[misc]  # Wagtail is untyped
    """`/<lang>/events/`: one per language, under that language's home page.

    The listing, calendar and feed routes come with EVT.6 and EVT.9.
    """

    intro = RichTextField(
        _("introduction"),
        features=RICH_TEXT_FEATURES,
        blank=True,
        help_text=_(
            "A few words shown at the top of the events list. "
            "If you leave it empty, the list starts right under the title."
        ),
    )

    content_panels: ClassVar[list[Any]] = [*Page.content_panels, FieldPanel("intro")]
    parent_page_types: ClassVar[list[str]] = ["cms.HomePage"]
    subpage_types: ClassVar[list[str]] = ["events.EventPage"]
    max_count_per_parent = 1

    class Meta:
        verbose_name = _("events index")

    def events(self) -> list["EventPage"]:
        """The live, public, not hidden events of an active vendor (or of the platform) in this
        index's language, soonest first (spec 15 §Status).

        A minimal list: the EVT.6 listing rules (upcoming only, filters, months, pagination,
        the original-language fallback) replace it.
        """
        pages = (
            EventPage.objects.child_of(self)
            .live()
            .public()
            .filter(hidden_at__isnull=True)
            .filter(Q(vendor__isnull=True) | Q(vendor__status=Vendor.Status.ACTIVE))
            .select_related("vendor")
        )
        return sorted(pages, key=lambda page: page.sort_key())

    def get_context(self, request: HttpRequest, *args: Any, **kwargs: Any) -> dict[str, Any]:
        context: dict[str, Any] = super().get_context(request, *args, **kwargs)
        context["events"] = self.events()
        return context


class EventPageQuerySet(PageQuerySet, VendorScopedQuerySet["EventPage"]):  # type: ignore[misc]
    """Wagtail's page queryset (`live()`, `child_of()` ...) with the CORE.3 vendor scoping."""


class EventPageManager(PageManager, VendorScopedManager["EventPage"]):  # type: ignore[misc]
    """A `PageManager` (Wagtail requires it) that is also the CORE.3 scoped manager."""

    _queryset_class = EventPageQuerySet


# Structured fields: identical in every translation, never overridden by a translator.
SYNCHRONISED_FIELDS = (
    "vendor",
    "event_type",
    "original_locale",
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
    "listing_image",
    "sequence",
    "cancelled_at",
    "hidden_at",
    "hidden_by",
    "hidden_reason",
    "dsa_notice_ref",
    "content_changed_at",
)

# Page-level state, written by the services on the page row: a revision never carries it back.
ROW_FIELDS = (
    "status",
    "sequence",
    "content_changed_at",
    "hidden_at",
    "hidden_by",
    "hidden_reason",
    "dsa_notice_ref",
)

ADDRESS_FIELDS = ("street", "postcode", "city", "region", "country")
LATITUDE_RANGE = (Decimal(-90), Decimal(90))
LONGITUDE_RANGE = (Decimal(-180), Decimal(180))
_COUNT = r"(^|;)\s*COUNT\s*="
_UNTIL = r"(^|;)\s*UNTIL\s*="


def _no_location() -> Q:
    return Q(
        latitude__isnull=True,
        longitude__isnull=True,
        venue_name="",
        booth="",
        **{name: "" for name in ADDRESS_FIELDS},
    )


def _constraints() -> list[models.BaseConstraint]:
    timed = Q(
        all_day=False,
        starts_at__isnull=False,
        ends_at__isnull=False,
        start_date__isnull=True,
        end_date__isnull=True,
    )
    all_day = Q(
        all_day=True,
        start_date__isnull=False,
        end_date__isnull=False,
        starts_at__isnull=True,
        ends_at__isnull=True,
    )
    return [
        models.CheckConstraint(condition=timed | all_day, name="events_eventpage_schedule_kind"),
        models.CheckConstraint(
            condition=Q(all_day=True) | Q(ends_at__gt=F("starts_at")),
            name="events_eventpage_ends_after_start",
        ),
        models.CheckConstraint(
            condition=Q(all_day=False) | Q(end_date__gte=F("start_date")),
            name="events_eventpage_end_date_not_before_start",
        ),
        models.CheckConstraint(
            condition=Q(all_day=False, exdate_days=[]) | Q(all_day=True, exdates=[]),
            name="events_eventpage_exdates_match_kind",
        ),
        models.CheckConstraint(
            condition=~(Q(rrule__iregex=_COUNT) & Q(rrule__iregex=_UNTIL)),
            name="events_eventpage_count_or_until",
        ),
        models.CheckConstraint(
            condition=Q(event_type__in=EventType.values), name="events_eventpage_type_valid"
        ),
        models.CheckConstraint(
            condition=Q(event_type=EventType.PROMOTION, attendance="")
            | (~Q(event_type=EventType.PROMOTION) & Q(attendance__in=Attendance.values)),
            name="events_eventpage_attendance_valid",
        ),
        models.CheckConstraint(
            condition=Q(status__in=EventStatus.values), name="events_eventpage_status_valid"
        ),
        models.CheckConstraint(
            condition=Q(geo_source__in=GeoSource.values), name="events_eventpage_geo_source_valid"
        ),
        # Regression case 19: both coordinates or neither, each in its range.
        models.CheckConstraint(
            condition=Q(latitude__isnull=True, longitude__isnull=True)
            | Q(latitude__isnull=False, longitude__isnull=False),
            name="events_eventpage_coordinates_pair",
        ),
        models.CheckConstraint(
            condition=Q(latitude__gte=LATITUDE_RANGE[0], latitude__lte=LATITUDE_RANGE[1]),
            name="events_eventpage_latitude_range",
        ),
        models.CheckConstraint(
            condition=Q(longitude__gte=LONGITUDE_RANGE[0], longitude__lte=LONGITUDE_RANGE[1]),
            name="events_eventpage_longitude_range",
        ),
        models.CheckConstraint(
            condition=~Q(event_type=EventType.PROMOTION) | (_no_location() & Q(online_url="")),
            name="events_eventpage_promotion_no_location",
        ),
        models.CheckConstraint(
            condition=~Q(attendance=Attendance.ONLINE) | _no_location(),
            name="events_eventpage_online_no_venue",
        ),
        models.CheckConstraint(
            condition=Q(online_url="")
            | (
                Q(attendance__in=[Attendance.ONLINE, Attendance.MIXED])
                & Q(online_url__startswith="https://")
            ),
            name="events_eventpage_online_url_https",
        ),
        models.CheckConstraint(
            condition=Q(geo_confidence__gte=0, geo_confidence__lte=1),
            name="events_eventpage_geo_confidence_range",
        ),
    ]


class HiddenEventPublishRefused(PermissionDenied):
    """A hidden event is published again only through `restore_event` (spec 15 "Hiding")."""


class EventPage(CountdownCacheMixin, Page):  # type: ignore[misc]  # Wagtail is untyped
    """One event in one language (spec 15 "Page models"); its translations share the
    `translation_key`. Written by vendors through the EVT.3 services and by staff in the admin."""

    EventType = EventType
    Attendance = Attendance
    GeoSource = GeoSource
    Status = EventStatus

    vendor_scope: ClassVar[str] = "vendor"
    objects = EventPageManager()

    # The id the vendor area and the API show (like BaseModel's); each translation has its own,
    # so a copy for translation gets a new one.
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    vendor = models.ForeignKey(
        Vendor,
        verbose_name=_("vendor"),
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        help_text=_(
            "The shop that organises the event. Leave it empty for an event of the platform "
            "itself, "
            "which only the platform staff can see and edit."
        ),
    )
    event_type = models.CharField(
        _("type"),
        max_length=16,
        choices=EventType.choices,
        help_text=_(
            "Choose what kind of event it is: a promotion (an offer, with no place), "
            "a trade fair or meeting, or something else. Required."
        ),
    )
    original_locale = models.ForeignKey(
        "wagtailcore.Locale",
        verbose_name=_("original language"),
        on_delete=models.PROTECT,
        related_name="+",
        help_text=_(
            "The language the event was first written in. It is set when the event is created "
            "and you cannot change it."
        ),
    )
    summary = models.CharField(
        _("summary"),
        max_length=300,
        help_text=_(
            "One or two sentences that tell what the event is, up to 300 characters. "
            "They are shown in the events list, in calendar feeds and in search results. "
            "Required: the event cannot be saved without it."
        ),
    )

    # When: timed events use timestamps, all-day events dates (inclusive end).
    all_day = models.BooleanField(
        _("all day"),
        default=False,
        help_text=_(
            "Tick it for an event with no set hours, such as a sale lasting several days. "
            "You then give the first and last day instead of the start and end times."
        ),
    )
    starts_at = models.DateTimeField(
        _("starts at"),
        null=True,
        blank=True,
        help_text=_(
            "The day and hour the event begins, in the time zone below. "
            "Required unless the event lasts all day."
        ),
    )
    ends_at = models.DateTimeField(
        _("ends at"),
        null=True,
        blank=True,
        help_text=_(
            "The day and hour the event ends. It must be after the start. "
            "Required unless the event lasts all day."
        ),
    )
    start_date = models.DateField(
        _("first day"),
        null=True,
        blank=True,
        help_text=_("The first day of an all-day event. Required when 'All day' is ticked."),
    )
    end_date = models.DateField(
        _("last day"),
        null=True,
        blank=True,
        help_text=_(
            "The last day of an all-day event, included. Use the same day as the first day for "
            "a one-day event. Required when 'All day' is ticked."
        ),
    )
    timezone = models.CharField(
        _("time zone"),
        max_length=64,
        default=default_timezone,
        help_text=_(
            "The time zone where the event takes place, for example 'Europe/Rome'. "
            "The start and end times above are read in it."
        ),
    )
    rrule = models.CharField(
        _("recurrence rule"),
        max_length=500,
        blank=True,
        help_text=_(
            "Only for an event that repeats. Write the rule, for example FREQ=YEARLY;COUNT=3 "
            "repeats it every year, three times. If you leave it empty, the event happens once."
        ),
    )
    exdates = ArrayField(
        models.DateTimeField(),
        verbose_name=_("excluded occurrences"),
        default=list,
        blank=True,
        help_text=_(
            "Dates and times of a repeating event that are skipped, for example a holiday. "
            "If you leave it empty, nothing is skipped."
        ),
    )
    exdate_days = ArrayField(
        models.DateField(),
        verbose_name=_("excluded days"),
        default=list,
        blank=True,
        help_text=_(
            "Days of a repeating all-day event that are skipped, for example a holiday. "
            "If you leave it empty, no day is skipped."
        ),
    )

    # Where.
    attendance = models.CharField(
        _("attendance"),
        max_length=16,
        choices=Attendance.choices,
        blank=True,
        help_text=_(
            "How people take part: in person, online or both. "
            "Required for every event except a promotion, which has no place."
        ),
    )
    online_url = models.URLField(
        _("online link"),
        max_length=500,
        blank=True,
        help_text=_(
            "Where people join an online event, starting with https://. "
            "Only for online or mixed events; leave it empty for the others."
        ),
    )
    venue_name = models.CharField(
        _("venue"),
        max_length=200,
        blank=True,
        help_text=_(
            "The name of the place, for example 'Fiera di Milano'. "
            "If you leave it empty, only the address is shown."
        ),
    )
    street = models.CharField(
        _("street"),
        max_length=200,
        blank=True,
        help_text=_(
            "The street and number, for example 'Via Roma 12'. "
            "It is also used to find the place on the map."
        ),
    )
    postcode = models.CharField(
        _("postcode"),
        max_length=16,
        blank=True,
        help_text=_("The postal code of the place, for example '20121'."),
    )
    city = models.CharField(
        _("city"),
        max_length=100,
        blank=True,
        help_text=_("The town or city of the place, for example 'Milano'."),
    )
    region = models.CharField(
        _("region"),
        max_length=100,
        blank=True,
        help_text=_("The region or province of the place, for example 'Lombardia'. Optional."),
    )
    country = CountryField(
        _("country"),
        blank=True,
        help_text=_("The country of the place. It helps find the right spot on the map."),
    )
    booth = models.CharField(
        _("booth"),
        max_length=50,
        blank=True,
        help_text=_(
            "Where to find you inside a fair, for example 'Hall 3, stand B12'. "
            "If you leave it empty, no stand is shown."
        ),
    )
    latitude = models.DecimalField(
        _("latitude"),
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        help_text=_(
            "The north-south position of the place, a number from -90 to 90, for example "
            "45.464200. "
            "Click the map to fill it in. Give both latitude and longitude, or neither."
        ),
    )
    longitude = models.DecimalField(
        _("longitude"),
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        help_text=_(
            "The east-west position of the place, a number from -180 to 180, for example 9.190000. "
            "Click the map to fill it in. Give both latitude and longitude, or neither."
        ),
    )
    geo_source = models.CharField(
        _("coordinates source"),
        max_length=16,
        choices=GeoSource.choices,
        blank=True,
        help_text=_(
            "How the position was found: from the address, placed on the map or typed by hand. "
            "It is filled in for you."
        ),
    )
    geo_provider = models.CharField(
        _("geocoding provider"),
        max_length=32,
        blank=True,
        help_text=_("The service that found the position from the address. Filled in for you."),
    )
    geo_precision = models.CharField(
        _("geocoding precision"),
        max_length=16,
        blank=True,
        help_text=_(
            "How exact the position found from the address is, for example a street or a town. "
            "Filled in for you."
        ),
    )
    geo_confidence = models.FloatField(
        _("geocoding confidence"),
        null=True,
        blank=True,
        help_text=_(
            "How sure the service is about the position, from 0 (not sure) to 1 (very sure). "
            "Filled in for you."
        ),
    )
    geo_at = models.DateTimeField(
        _("geocoded at"),
        null=True,
        blank=True,
        help_text=_("When the position was found from the address. Filled in for you."),
    )

    # Links and image.
    products = ParentalManyToManyField(
        "catalog.Product",
        verbose_name=_("products"),
        blank=True,
        related_name="event_pages",
        help_text=_(
            "The products you bring or put on offer at this event. "
            "You can only choose your own products. Leave it empty if none applies."
        ),
    )
    categories = ParentalManyToManyField(
        "catalog.Category",
        verbose_name=_("categories"),
        blank=True,
        related_name="event_pages",
        help_text=_(
            "The product categories the event is about, so visitors can find it from them. "
            "Leave it empty if none applies."
        ),
    )
    listing_image = models.ForeignKey(
        get_image_model_string(),
        verbose_name=_("listing image"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text=_(
            "The picture shown next to the event in lists and feeds. "
            "If you leave it empty, the event is listed without a picture."
        ),
    )

    # State: `status` is denormalised (services.sync_event_status) from each page's own `live`
    # and workflow state, so a draft translation of a live event is a draft. Not editable: no
    # form writes it, and wagtail-localize leaves it out of the synchronised copy.
    status = models.CharField(
        _("status"),
        max_length=16,
        choices=EventStatus.choices,
        default=EventStatus.DRAFT,
        db_index=True,
        editable=False,
    )
    sequence = models.PositiveIntegerField(
        _("ICS sequence"),
        default=0,
        help_text=_(
            "Counts how many times the date, time or place changed after publishing, so that "
            "people's calendar apps update their copy. The platform fills it in."
        ),
    )
    cancelled_at = models.DateTimeField(
        _("cancelled at"),
        null=True,
        blank=True,
        help_text=_(
            "When the event was cancelled. Empty for an event that is going ahead. "
            "The platform fills it in."
        ),
    )
    hidden_at = models.DateTimeField(
        _("hidden at"),
        null=True,
        blank=True,
        help_text=_(
            "When the platform staff hid the event from the public. Empty for a visible event. "
            "The platform fills it in."
        ),
    )
    hidden_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("hidden by"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text=_("The staff member who hid the event. The platform fills it in."),
    )
    hidden_reason = models.TextField(
        _("reason for hiding"),
        blank=True,
        help_text=_("Why the event was hidden. The vendor can read it. The platform fills it in."),
    )
    dsa_notice_ref = models.CharField(
        _("DSA notice"),
        max_length=64,
        blank=True,
        help_text=_(
            "The reference of the report that led to hiding the event, if there was one. "
            "The platform fills it in."
        ),
    )
    content_changed_at = models.DateTimeField(
        _("content changed at"),
        null=True,
        blank=True,
        help_text=_(
            "The last time a change that matters to calendars was published. "
            "The platform fills it in."
        ),
    )

    body = StreamField(
        event_blocks(),
        blank=True,
        verbose_name=_("body"),
        help_text=_(
            "The full description of the event, built from blocks: text, pictures, questions "
            "and a countdown. Drag blocks to reorder them. If you add none, only the summary is "
            "shown."
        ),
    )

    override_translatable_fields: ClassVar[list[SynchronizedField]] = [
        SynchronizedField(name, overridable=False) for name in SYNCHRONISED_FIELDS
    ]
    content_panels: ClassVar[list[Any]] = event_content_panels()
    base_form_class = EventPageForm
    parent_page_types: ClassVar[list[str]] = ["events.EventIndexPage"]
    subpage_types: ClassVar[list[str]] = []
    exclude_fields_in_copy: ClassVar[list[str]] = ["public_id"]

    class Meta:
        verbose_name = _("event")
        constraints = _constraints()

    # --- Moderation (spec 15 "Moderation with Wagtail workflows", EVT.3)

    def review_needed(self, *, staff: bool = False) -> bool:
        """Whether a submission of this event goes through "Event review" (`engine.needs_review`).

        `staff`: the submitter is platform staff. A platform event (no vendor) is never
        reviewed. The threshold is the page's site's `EventSettings` (the default site's when the
        page is outside every site), and the count is the vendor's approved events.
        """
        if staff or self.vendor_id is None:
            return False
        mode = (
            VendorEventPolicy.objects.filter(vendor_id=self.vendor_id)
            .values_list("mode", flat=True)
            .first()
        ) or PolicyMode.DEFAULT
        threshold = EventSettings.current(self._settings_site()).moderation_threshold
        approved = approved_event_count(self.vendor_id) if mode == PolicyMode.DEFAULT else 0
        return engine.needs_review(approved, threshold, mode, is_staff=False)

    def _settings_site(self) -> Site:
        site: Site | None = self.get_site() if self.pk else None
        return site or Site.objects.get(is_default_site=True)

    def get_workflow(self) -> Workflow | None:
        """Wagtail hook: "Event review" while the vendor needs review, else none (publish at once).

        Replaces Wagtail's tree-based lookup, so a workflow assigned to an ancestor page never
        applies to events.
        """
        if not getattr(settings, "WAGTAIL_WORKFLOW_ENABLED", True) or not self.review_needed():
            return None
        workflow = event_review_workflow()
        return workflow if workflow.active else None

    @property
    def has_workflow(self) -> bool:
        return self.get_workflow() is not None

    # --- Publishing: the ICS SEQUENCE rule (spec 15 "Services")

    def with_content_json(self, content: dict[str, Any]) -> "EventPage":
        """A revision applied over this page, keeping the page-level state (`ROW_FIELDS`).

        Hiding, the sequence and the denormalised status belong to the page, not to a revision:
        an old revision never clears a hiding or rolls the sequence back. A cancellation sticks
        once published.
        """
        page: EventPage = super().with_content_json(content)
        for name in ROW_FIELDS:
            attname = self._meta.get_field(name).attname
            setattr(page, attname, getattr(self, attname))
        if self.cancelled_at is not None:
            page.cancelled_at = self.cancelled_at
        return page

    def publish(
        self,
        revision: Any,
        user: Any = None,
        changed: bool = True,
        log_action: bool = True,
        previous_revision: Any = None,
        skip_permission_checks: bool = False,
    ) -> Any:
        """Publish `revision`, bumping `sequence` first when it changes a field the ICS shows.

        Every publish goes through here: the services, Wagtail's admin, the workflow approval,
        scheduled publishing and wagtail-localize. A hidden page is published again only by
        `restore_event` (spec 15 "Hiding"), whatever path the publish takes: the stored row
        decides, and only `restore_event` clears `hidden_at` before it republishes.
        """
        if EventPage.objects.filter(pk=self.pk, hidden_at__isnull=False).exists():
            raise HiddenEventPublishRefused("a hidden event is restored by platform staff only")
        self._bump_sequence(revision)
        return super().publish(
            revision,
            user=user,
            changed=changed,
            log_action=log_action,
            previous_revision=previous_revision,
            skip_permission_checks=skip_permission_checks,
        )

    def _bump_sequence(self, revision: Any) -> None:
        stored: EventPage | None = EventPage.objects.filter(pk=self.pk).first()
        if stored is None:
            return
        published = stored.with_content_json(revision.content)
        sequence = engine.next_sequence(
            stored.sequence,
            ics_snapshot(stored),
            ics_snapshot(published),
            was_published=stored.first_published_at is not None,
        )
        if sequence == stored.sequence:
            return
        now = timezone.now()
        EventPage.objects.filter(pk=self.pk).update(sequence=sequence, content_changed_at=now)
        # The publish action builds the published page from the revision's own object.
        for target in (self, revision.content_object):
            target.sequence = sequence
            target.content_changed_at = now

    # --- Public routing (spec 15 §Status)

    def is_publicly_visible(self) -> bool:
        """Not hidden, and the vendor (if any) active: the rest of the public query is Wagtail's
        own (`live`, view restrictions)."""
        if self.hidden_at is not None:
            return False
        vendor = self.vendor
        return vendor is None or vendor.status == Vendor.Status.ACTIVE

    def route(self, request: HttpRequest, path_components: list[str]) -> Any:
        """A hidden event, or one of a suspended or closed vendor, is a 404 to visitors.

        `route` serves public URLs only: Wagtail's admin and vendor-area previews call `serve`
        directly, so staff and the vendor still preview such a page.
        """
        if not path_components and not self.is_publicly_visible():
            raise Http404
        return super().route(request, path_components)

    # --- Display (templates)

    def zone(self) -> zoneinfo.ZoneInfo:
        return zoneinfo.ZoneInfo(self.timezone)

    def sort_key(self) -> dt.datetime:
        """When the event starts, for ordering timed and all-day events together."""
        if self.starts_at is not None:
            return self.starts_at
        day = self.start_date or dt.date.max
        return dt.datetime.combine(day, dt.time.min, tzinfo=self.zone())

    def has_address(self) -> bool:
        return any(getattr(self, name) for name in ("venue_name", "booth", *ADDRESS_FIELDS))

    # --- Coordinates (EVT.12 candidates come through the core `Geocoder` protocol)

    def set_geocoded_point(self, result: GeocodeResult, *, at: dt.datetime) -> None:
        """Store the candidate the author confirmed, with its geocoding metadata."""
        self.latitude = result.latitude
        self.longitude = result.longitude
        self.geo_source = GeoSource.GEOCODED
        self.geo_provider = result.provider
        self.geo_precision = result.precision
        self.geo_confidence = result.confidence
        self.geo_at = at

    def set_point(self, latitude: Decimal, longitude: Decimal, *, source: str) -> None:
        """A point placed on the map or typed; earlier geocoding metadata is kept."""
        self.latitude = latitude
        self.longitude = longitude
        self.geo_source = source

    def clear_point(self) -> None:
        self.latitude = self.longitude = None
        self.geo_source = GeoSource.NONE
        self.geo_provider = self.geo_precision = ""
        self.geo_confidence = None
        self.geo_at = None

    # --- Validation (the same rules as the check constraints, as field errors)

    def clean(self) -> None:
        super().clean()
        errors: dict[str, list[Any]] = {}

        def add(field: str, message: Any) -> None:
            errors.setdefault(field, []).append(message)

        self._clean_schedule(add)
        self._clean_location(add)
        if errors:
            raise ValidationError(errors)

    def _clean_schedule(self, add: Any) -> None:
        if self.all_day:
            for field in ("start_date", "end_date"):
                if getattr(self, field) is None:
                    add(field, _("An all-day event needs its first and last day."))
            for field in ("starts_at", "ends_at"):
                if getattr(self, field) is not None:
                    add(field, _("An all-day event has days, not times."))
            if self.start_date and self.end_date and self.end_date < self.start_date:
                add("end_date", _("The last day cannot be before the first day."))
            if self.exdates:
                add("exdates", _("An all-day event excludes days, not times."))
        else:
            for field in ("starts_at", "ends_at"):
                if getattr(self, field) is None:
                    add(field, _("A timed event needs its start and end time."))
            for field in ("start_date", "end_date"):
                if getattr(self, field) is not None:
                    add(field, _("A timed event has times, not days."))
            if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
                add("ends_at", _("The event must end after it starts."))
            if self.exdate_days:
                add("exdate_days", _("A timed event excludes times, not days."))
        try:
            zoneinfo.ZoneInfo(self.timezone)
        except (zoneinfo.ZoneInfoNotFoundError, ValueError):
            add("timezone", _("Choose a time zone such as Europe/Rome."))
        if self.rrule:
            try:
                engine.validate_rrule(self.rrule, self.all_day)
            except engine.RecurrenceError as exc:
                add("rrule", str(exc))
                return
        if engine.has_count_and_until(self.rrule):
            add("rrule", _("A recurrence ends after a number of times or on a date, not both."))

    def _clean_location(self, add: Any) -> None:
        has_point = self.latitude is not None or self.longitude is not None
        if (self.latitude is None) != (self.longitude is None):
            add("longitude" if self.longitude is None else "latitude", _("Give both coordinates."))
        if self.latitude is not None and not (
            LATITUDE_RANGE[0] <= self.latitude <= LATITUDE_RANGE[1]
        ):
            add("latitude", _("The latitude is between -90 and 90."))
        if self.longitude is not None and not (
            LONGITUDE_RANGE[0] <= self.longitude <= LONGITUDE_RANGE[1]
        ):
            add("longitude", _("The longitude is between -180 and 180."))
        if self.geo_confidence is not None and not 0 <= self.geo_confidence <= 1:
            add("geo_confidence", _("The confidence is between 0 and 1."))

        place = ("venue_name", "booth", *ADDRESS_FIELDS)
        if self.event_type == EventType.PROMOTION:
            if self.attendance:
                add("attendance", _("A promotion has no attendance mode."))
            if has_point:
                add("latitude", _("A promotion has no location."))
            for field in (*place, "online_url"):
                if getattr(self, field):
                    add(field, _("A promotion has no location."))
            return
        if not self.attendance:
            add("attendance", _("Say whether the event is in person, online or both."))
        if self.attendance == Attendance.ONLINE:
            if has_point:
                add("latitude", _("An online event has no venue or coordinates."))
            for field in place:
                if getattr(self, field):
                    add(field, _("An online event has no venue or coordinates."))
            if not self.online_url:
                add("online_url", _("Give the link to the online event."))
        if self.online_url:
            if self.attendance not in (Attendance.ONLINE, Attendance.MIXED):
                add("online_url", _("Only online and mixed events have a link."))
            elif (problem := engine.https_url_problem(self.online_url, "The link")) is not None:
                add("online_url", problem)


# --- Moderation policy and settings


class PolicyMode(models.TextChoices):
    DEFAULT = "default", _("Default threshold")
    ALWAYS_REVIEW = "always_review", _("Always review")
    TRUSTED = "trusted", _("Trusted: never review")


class VendorEventPolicy(BaseModel):
    """A vendor's moderation override (spec 15 "Moderation"). Changed by staff only, audited
    (`services.set_vendor_policy`, EVT.3)."""

    Mode = PolicyMode

    vendor_scope: ClassVar[str] = "vendor"
    objects: ClassVar[VendorScopedManager["VendorEventPolicy"]] = VendorScopedManager()

    vendor = models.OneToOneField(
        Vendor, verbose_name=_("vendor"), on_delete=models.CASCADE, related_name="event_policy"
    )
    mode = models.CharField(
        _("mode"), max_length=16, choices=PolicyMode.choices, default=PolicyMode.DEFAULT
    )

    class Meta:
        verbose_name = _("vendor event policy")
        verbose_name_plural = _("vendor event policies")
        constraints = [
            models.CheckConstraint(
                condition=Q(mode__in=PolicyMode.values),
                name="events_vendoreventpolicy_mode_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.vendor}: {self.mode}"


class EventSettings(BaseSiteSetting):  # type: ignore[misc]  # Wagtail is untyped
    """Per-site events settings, staff-editable and audited (EVT.3 services, part 2/2 admin)."""

    moderation_threshold = models.PositiveSmallIntegerField(
        _("moderation threshold"),
        default=3,
        help_text=_("A vendor's first events up to this number go through review."),
    )
    cancelled_retention_days = models.PositiveSmallIntegerField(
        _("days a cancelled event stays in feeds"), default=30
    )

    class Meta:
        verbose_name = _("events settings")
        verbose_name_plural = _("events settings")

    @classmethod
    def current(cls, site: Site) -> "EventSettings":
        """The settings of `site`, or unsaved defaults: a read never creates the row.

        Wagtail's `for_site` creates it, which would make a GET (an admin page, the workflow
        lookup) write. Only `set_event_settings` saves it.
        """
        found: EventSettings | None = cls.objects.filter(site=site).first()
        return found if found is not None else cls(site=site)


# --- The "Event review" workflow (spec 15 "Moderation with Wagtail workflows")

EVENT_REVIEW = "Event review"
EVENT_MODERATORS = roles.EVENT_MODERATORS


def event_review_workflow() -> Workflow:
    """The "Event review" workflow: one `GroupApprovalTask` for the "Event moderators" group.

    Created after every `migrate` and on first use when missing; never changed once it exists,
    so staff may edit its task and groups in the admin. The CORE.8 editor roles take over the
    group.
    """
    workflow: Workflow | None = Workflow.objects.filter(name=EVENT_REVIEW).order_by("pk").first()
    if workflow is not None:
        return workflow
    with transaction.atomic():
        group, _ = Group.objects.get_or_create(name=EVENT_MODERATORS)
        task = GroupApprovalTask.objects.create(name=EVENT_REVIEW)
        task.groups.add(group)
        workflow = Workflow.objects.create(name=EVENT_REVIEW)
        WorkflowTask.objects.create(workflow=workflow, task=task, sort_order=0)
    return workflow


def approved_event_count(vendor_id: int) -> int:
    """The vendor's events with at least one approved workflow state (spec 15 "Threshold").

    Events, not translations: the pages of one event share a `translation_key`. Rejected and
    withdrawn submissions do not count.
    """
    keys = {
        str(pk): key
        for pk, key in EventPage.objects.filter(vendor_id=vendor_id).values_list(
            "pk", "translation_key"
        )
    }
    approved = WorkflowState.objects.filter(
        base_content_type=ContentType.objects.get_for_model(Page),
        object_id__in=list(keys),
        status=WorkflowState.STATUS_APPROVED,
    ).values_list("object_id", flat=True)
    return len({keys[object_id] for object_id in approved})


def _comparable(page: EventPage, name: str) -> Any:
    field = page._meta.get_field(name)
    value = getattr(page, field.attname)
    if isinstance(field, ArrayField):
        return [field.base_field.to_python(item) for item in value or []]
    return field.to_python(value)


def ics_snapshot(page: EventPage) -> dict[str, Any]:
    """The values of the fields the ICS shows (`engine.ICS_FIELDS`), comparable across a
    stored page and a page restored from a revision."""
    return {name: _comparable(page, name) for name in engine.ICS_FIELDS}
