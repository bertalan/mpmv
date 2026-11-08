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
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.http import Http404, HttpRequest
from django.utils.translation import gettext_lazy as _
from modelcluster.fields import ParentalManyToManyField
from wagtail import blocks
from wagtail.admin.panels import FieldPanel
from wagtail.contrib.settings.models import BaseSiteSetting
from wagtail.fields import RichTextField, StreamField
from wagtail.images import get_image_model_string
from wagtail.models import Page, PageManager, PageQuerySet
from wagtail_localize.fields import SynchronizedField

from apps.cms.blocks import (
    RICH_TEXT_FEATURES,
    CallToActionBlock,
    FaqBlock,
    FigureBlock,
    GalleryBlock,
)
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

    The CMS.10 product grid and the CMS.9 countdown join when those tasks add them; embeds only
    with the SEO.5 consent rules. EVT.3 restricts vendors to the vendor-safe subset.
    """
    return [
        ("paragraph", blocks.RichTextBlock(features=RICH_TEXT_FEATURES, label=_("Rich text"))),
        ("figure", FigureBlock()),
        ("gallery", GalleryBlock()),
        ("cta", CallToActionBlock()),
        ("faq", FaqBlock()),
    ]


# --- Pages


class EventIndexPage(Page):  # type: ignore[misc]  # Wagtail is untyped
    """`/<lang>/events/`: one per language, under that language's home page.

    The listing, calendar and feed routes come with EVT.6 and EVT.9.
    """

    intro = RichTextField(_("introduction"), features=RICH_TEXT_FEATURES, blank=True)

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


class EventPage(Page):  # type: ignore[misc]  # Wagtail is untyped
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
        help_text=_("Empty for a platform event (staff only)."),
    )
    event_type = models.CharField(_("type"), max_length=16, choices=EventType.choices)
    original_locale = models.ForeignKey(
        "wagtailcore.Locale",
        verbose_name=_("original language"),
        on_delete=models.PROTECT,
        related_name="+",
    )
    summary = models.CharField(
        _("summary"), max_length=300, help_text=_("Shown in listings, feeds and search results.")
    )

    # When: timed events use timestamps, all-day events dates (inclusive end).
    all_day = models.BooleanField(_("all day"), default=False)
    starts_at = models.DateTimeField(_("starts at"), null=True, blank=True)
    ends_at = models.DateTimeField(_("ends at"), null=True, blank=True)
    start_date = models.DateField(_("first day"), null=True, blank=True)
    end_date = models.DateField(_("last day"), null=True, blank=True)
    timezone = models.CharField(_("time zone"), max_length=64, default=default_timezone)
    rrule = models.CharField(
        _("recurrence rule"),
        max_length=500,
        blank=True,
        help_text=_("An RFC 5545 RRULE, for example FREQ=YEARLY;COUNT=3."),
    )
    exdates = ArrayField(
        models.DateTimeField(), verbose_name=_("excluded occurrences"), default=list, blank=True
    )
    exdate_days = ArrayField(
        models.DateField(), verbose_name=_("excluded days"), default=list, blank=True
    )

    # Where.
    attendance = models.CharField(
        _("attendance"), max_length=16, choices=Attendance.choices, blank=True
    )
    online_url = models.URLField(_("online link"), max_length=500, blank=True)
    venue_name = models.CharField(_("venue"), max_length=200, blank=True)
    street = models.CharField(_("street"), max_length=200, blank=True)
    postcode = models.CharField(_("postcode"), max_length=16, blank=True)
    city = models.CharField(_("city"), max_length=100, blank=True)
    region = models.CharField(_("region"), max_length=100, blank=True)
    country = CountryField(_("country"), blank=True)
    booth = models.CharField(_("booth"), max_length=50, blank=True)
    latitude = models.DecimalField(
        _("latitude"), max_digits=9, decimal_places=6, null=True, blank=True
    )
    longitude = models.DecimalField(
        _("longitude"), max_digits=9, decimal_places=6, null=True, blank=True
    )
    geo_source = models.CharField(
        _("coordinates source"), max_length=16, choices=GeoSource.choices, blank=True
    )
    geo_provider = models.CharField(_("geocoding provider"), max_length=32, blank=True)
    geo_precision = models.CharField(_("geocoding precision"), max_length=16, blank=True)
    geo_confidence = models.FloatField(_("geocoding confidence"), null=True, blank=True)
    geo_at = models.DateTimeField(_("geocoded at"), null=True, blank=True)

    # Links and image.
    products = ParentalManyToManyField(
        "catalog.Product", verbose_name=_("products"), blank=True, related_name="event_pages"
    )
    categories = ParentalManyToManyField(
        "catalog.Category", verbose_name=_("categories"), blank=True, related_name="event_pages"
    )
    listing_image = models.ForeignKey(
        get_image_model_string(),
        verbose_name=_("listing image"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
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
    sequence = models.PositiveIntegerField(_("ICS sequence"), default=0)
    cancelled_at = models.DateTimeField(_("cancelled at"), null=True, blank=True)
    hidden_at = models.DateTimeField(_("hidden at"), null=True, blank=True)
    hidden_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("hidden by"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    hidden_reason = models.TextField(_("reason for hiding"), blank=True)
    dsa_notice_ref = models.CharField(_("DSA notice"), max_length=64, blank=True)
    content_changed_at = models.DateTimeField(_("content changed at"), null=True, blank=True)

    body = StreamField(event_blocks(), blank=True, verbose_name=_("body"))

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
            elif not self.online_url.startswith("https://"):
                add("online_url", _("The link must start with https://."))


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
