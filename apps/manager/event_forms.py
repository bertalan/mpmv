"""The vendor's event form (EVT.4): a thin validator, the services own the rules.

Times are typed as the wall clock of the event's time zone (no browser zone is involved), the
recurrence presets of `apps.events.engine` write the RRULE, and a promotion has no place.
"""

import datetime as dt
import zoneinfo
from typing import Any

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import Category, Product
from apps.core.geocoding import AddressQuery, GeocodeResult
from apps.core.geocoding import engine as geo_engine
from apps.core.models import ImageLicence
from apps.events import engine, services
from apps.events.models import EventPage, default_timezone
from apps.vendors.models import Vendor

CUSTOM = "custom"  # a stored rule the presets cannot write: kept as it is
_TIME_FORMATS = ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S")
_WEEKDAY_LABELS = (
    ("MO", _("Monday")),
    ("TU", _("Tuesday")),
    ("WE", _("Wednesday")),
    ("TH", _("Thursday")),
    ("FR", _("Friday")),
    ("SA", _("Saturday")),
    ("SU", _("Sunday")),
)
# The field an engine error shows on, with the vendor-facing message (the engine's are English).
_RULE_ERRORS = {
    "BYDAY": ("recurrence_weekdays", _("Choose at least one weekday.")),
    "BYMONTHDAY": ("recurrence_month_day", _("Choose a day from 1 to 31, or the last day.")),
    "COUNT": ("recurrence_count", _("Give how many times the event takes place.")),
    "UNTIL": ("recurrence_until", _("Give the last day.")),
    "INTERVAL": ("recurrence_interval", _("The interval is from 1 to 12.")),
    "FREQ": ("recurrence", _("Choose how the event repeats.")),
}
PLACE_FIELDS = (
    "attendance",
    "online_url",
    "venue_name",
    "street",
    "postcode",
    "city",
    "region",
    "country",
    "booth",
)
ADDRESS_QUERY_FIELDS = ("country", "postcode", "city", "street")
_DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


def product_label(product: Product) -> str:
    """The product's title in the active language (else any), with its SKU."""
    texts = {t.language: t.title for t in product.translations.all() if t.title}
    title = texts.get(get_language() or "en") or texts.get("en") or next(iter(texts.values()), "")
    return f"{title} ({product.sku})" if title else str(product.sku)


def category_label(category: Category) -> str:
    names = {t.language: t.name for t in category.translations.all() if t.name}
    name = names.get(get_language() or "en") or names.get("en") or next(iter(names.values()), "")
    return str(name or _("Category %(number)d") % {"number": category.pk})


class _CoordinateField(forms.CharField):
    """A latitude or longitude typed by hand or set by the map: six decimal places."""

    def __init__(self, *, limit: int, **kwargs: Any) -> None:
        super().__init__(required=False, max_length=20, **kwargs)
        self.limit = limit

    def clean(self, value: Any) -> Any:
        text = super().clean(value)
        if not text:
            return None
        try:
            return geo_engine.coordinate(text.strip().replace(",", "."), limit=self.limit)
        except ValueError:
            raise ValidationError(
                _("Give a number from -%(limit)d to %(limit)d, for example 45.464200."),
                params={"limit": self.limit},
            ) from None


class _WallClockField(forms.DateTimeField):
    """A date and time kept as typed (naive): the form reads it in the event's own zone."""

    widget = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")

    def to_python(self, value: Any) -> dt.datetime | None:
        if value in self.empty_values:
            return None
        if isinstance(value, dt.datetime):
            return value.replace(tzinfo=None)
        for fmt in _TIME_FORMATS:
            try:
                return dt.datetime.strptime(str(value).strip(), fmt)
            except ValueError:
                continue
        raise ValidationError(self.error_messages["invalid"], code="invalid")


class EventForm(forms.Form):
    title = forms.CharField(
        label=_("Title"),
        max_length=255,
        help_text=_("The name of the event, as shoppers see it. Required."),
    )
    summary = forms.CharField(
        label=_("Summary"),
        max_length=300,
        help_text=_(
            "One or two sentences that tell what the event is, up to 300 characters. They show "
            "in the events list and in search results. Required."
        ),
    )
    event_type = forms.ChoiceField(
        label=_("Type"),
        choices=EventPage.EventType.choices,
        help_text=_(
            "A promotion (an offer, with no place), a trade fair or meeting, or something else. "
            "Required."
        ),
    )
    all_day = forms.BooleanField(
        label=_("All day"),
        required=False,
        help_text=_("Tick it for an event with no set hours. Then give the first and last day."),
    )
    starts_at = _WallClockField(
        label=_("Starts at"),
        required=False,
        help_text=_(
            "Day and hour the event begins, in the time zone below. Not for all-day events."
        ),
    )
    ends_at = _WallClockField(
        label=_("Ends at"),
        required=False,
        help_text=_("Day and hour the event ends, after the start. Not for all-day events."),
    )
    start_date = forms.DateField(
        label=_("First day"),
        required=False,
        widget=_DATE,
        help_text=_("The first day of an all-day event."),
    )
    end_date = forms.DateField(
        label=_("Last day"),
        required=False,
        widget=_DATE,
        help_text=_("The last day of an all-day event, included. Same as the first for one day."),
    )
    timezone = forms.CharField(
        label=_("Time zone"),
        max_length=64,
        help_text=_(
            "Where the event takes place, for example Europe/Rome. The times above are read in it."
        ),
    )
    recurrence = forms.ChoiceField(
        label=_("Repeats"),
        choices=(
            (engine.PRESET_NONE, _("Does not repeat")),
            (engine.PRESET_YEARLY, _("Every year, on the same dates")),
            (engine.PRESET_WEEKLY, _("Every week, on these days")),
            (engine.PRESET_MONTHLY, _("Every month, on one day")),
        ),
        initial=engine.PRESET_NONE,
        help_text=_("Choose how the event repeats. The fields below belong to the choice."),
    )
    recurrence_weekdays = forms.MultipleChoiceField(
        label=_("Weekdays"),
        required=False,
        choices=_WEEKDAY_LABELS,
        widget=forms.CheckboxSelectMultiple,
        help_text=_("For a weekly event: the days of the week it takes place."),
    )
    recurrence_month_day = forms.IntegerField(
        label=_("Day of the month"),
        required=False,
        min_value=-1,
        max_value=31,
        help_text=_("For a monthly event: a day from 1 to 31, or -1 for the last day."),
    )
    recurrence_interval = forms.IntegerField(
        label=_("Every how many"),
        required=False,
        min_value=1,
        max_value=12,
        initial=1,
        help_text=_("1 repeats every time, 2 every second time, up to 12. Empty means 1."),
    )
    recurrence_ends = forms.ChoiceField(
        label=_("It ends"),
        required=False,
        choices=(
            (engine.ENDS_NEVER, _("Never")),
            (engine.ENDS_COUNT, _("After a number of times")),
            (engine.ENDS_UNTIL, _("On a last day")),
        ),
        initial=engine.ENDS_NEVER,
        help_text=_("When the repetition stops. Fill in the field that belongs to your choice."),
    )
    recurrence_count = forms.IntegerField(
        label=_("Number of times"),
        required=False,
        min_value=1,
        max_value=500,
        help_text=_("For an event that ends after a number of times."),
    )
    recurrence_until = forms.DateField(
        label=_("Last day of the repetition"),
        required=False,
        widget=_DATE,
        help_text=_("For an event that ends on a last day."),
    )
    attendance = forms.ChoiceField(
        label=_("Attendance"),
        required=False,
        choices=[("", "---------"), *EventPage.Attendance.choices],
        help_text=_(
            "In person, online or both. Required except for a promotion, which has no place."
        ),
    )
    online_url = forms.URLField(
        label=_("Online link"),
        required=False,
        max_length=500,
        help_text=_("Where people join an online event, starting with https://."),
    )
    venue_name = forms.CharField(
        label=_("Venue"),
        required=False,
        max_length=200,
        help_text=_("The name of the place, for example Fiera di Milano."),
    )
    street = forms.CharField(
        label=_("Street"),
        required=False,
        max_length=200,
        help_text=_("The street and number, for example Via Roma 12."),
    )
    postcode = forms.CharField(
        label=_("Postcode"), required=False, max_length=16, help_text=_("The postal code.")
    )
    city = forms.CharField(label=_("City"), required=False, max_length=100)
    region = forms.CharField(
        label=_("Region"),
        required=False,
        max_length=100,
        help_text=_("The region or province. Optional."),
    )
    country = forms.CharField(
        label=_("Country"),
        required=False,
        max_length=2,
        help_text=_("The country of the place, as a two-letter code, for example IT."),
    )
    booth = forms.CharField(
        label=_("Booth or stand"),
        required=False,
        max_length=100,
        help_text=_("Where to find you at a fair, for example Hall 3, stand B12. Optional."),
    )

    latitude = _CoordinateField(
        limit=90,
        label=_("Latitude"),
        help_text=_(
            "The north-south position, from -90 to 90, for example 45.464200. The map fills it "
            "in. Give both latitude and longitude, or neither."
        ),
    )
    longitude = _CoordinateField(
        limit=180,
        label=_("Longitude"),
        help_text=_(
            "The east-west position, from -180 to 180, for example 9.190000. The map fills it "
            "in. Give both latitude and longitude, or neither."
        ),
    )
    # Set by the map picker (hidden): how the point was found, and the "Find coordinates"
    # candidate the author chose before moving the marker. Checked against the cached lookup.
    geo_source = forms.CharField(required=False, widget=forms.HiddenInput)
    picked_latitude = forms.CharField(required=False, widget=forms.HiddenInput)
    picked_longitude = forms.CharField(required=False, widget=forms.HiddenInput)
    products = forms.ModelMultipleChoiceField(
        queryset=Product.objects.none(),
        required=False,
        label=_("Products"),
        widget=forms.CheckboxSelectMultiple,
        help_text=_("The products you bring or put on offer. Only your own products. Optional."),
    )
    categories: forms.ModelMultipleChoiceField[Category] = forms.ModelMultipleChoiceField(
        queryset=Category.objects.none(),
        required=False,
        label=_("Categories"),
        widget=forms.CheckboxSelectMultiple,
        help_text=_(
            "The categories of your products that the event is about, so visitors find it from "
            "them. Optional."
        ),
    )

    SECTIONS = (
        (_("The event"), ("title", "summary", "event_type")),
        (
            _("When"),
            ("all_day", "starts_at", "ends_at", "start_date", "end_date", "timezone"),
        ),
        (
            _("Repetition"),
            (
                "recurrence",
                "recurrence_weekdays",
                "recurrence_month_day",
                "recurrence_interval",
                "recurrence_ends",
                "recurrence_count",
                "recurrence_until",
            ),
        ),
        (_("Where"), PLACE_FIELDS),
        (_("Products and categories"), ("products", "categories")),
    )
    POINT_FIELDS = ("latitude", "longitude")
    PICKER_INPUTS = ("latitude", "longitude", "street", "postcode", "city", "country")
    PICKED_INPUTS = ("picked_latitude", "picked_longitude")

    def __init__(
        self,
        *args: Any,
        kept_rule: str = "",
        vendor: Vendor | None = None,
        page: EventPage | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.page = page if page is not None else EventPage()
        self.picked: services.PickedPoint | None = None
        if vendor is not None:
            linked = [c.pk for c in page.categories.all()] if page is not None and page.pk else []
            products = services.vendor_products(vendor).prefetch_related("translations")
            categories = services.vendor_categories(vendor, keep=linked).prefetch_related(
                "translations"
            )
            self.fields["products"].queryset = products  # type: ignore[attr-defined]
            self.fields["products"].label_from_instance = product_label  # type: ignore[attr-defined]
            self.fields["categories"].queryset = categories  # type: ignore[attr-defined]
            self.fields["categories"].label_from_instance = category_label  # type: ignore[attr-defined]
        self.kept_rule = kept_rule  # a rule the presets cannot write, kept by "custom"
        if kept_rule:
            self.fields["recurrence"].choices = [  # type: ignore[attr-defined]
                *self.fields["recurrence"].choices,  # type: ignore[attr-defined]
                (CUSTOM, _("Keep the rule already saved: %(rule)s") % {"rule": kept_rule}),
            ]
        self.fields["timezone"].initial = default_timezone()

    def sections(self) -> list[tuple[Any, list[forms.BoundField]]]:
        return [(title, [self[name] for name in names]) for title, names in self.SECTIONS]

    def point_fields(self) -> list[forms.BoundField]:
        return [self[name] for name in self.POINT_FIELDS]

    def picker_context(self) -> dict[str, Any]:
        """What `events/admin/map_picker_panel.html` reads (the ids of the form's inputs)."""
        inputs = {name: self[name].auto_id for name in self.PICKER_INPUTS}
        picked = {name.replace("_", "-"): self[name].auto_id for name in self.PICKED_INPUTS}
        return {
            "inputs": inputs,
            "picked_inputs": picked,
            "source_input": self["geo_source"].auto_id,
        }

    # --- Validation

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        zone = self._zone(data)
        all_day = bool(data.get("all_day"))
        if zone is not None and not all_day:
            for name in ("starts_at", "ends_at"):
                if data.get(name) is not None:
                    data[name] = data[name].replace(tzinfo=zone)
        data["rrule"] = self._rrule(data, all_day, zone)
        self._clean_point(data)
        return data

    def _chosen_point(self, data: dict[str, Any]) -> tuple[Any, Any] | None:
        try:
            return (
                geo_engine.coordinate(str(data.get("picked_latitude") or "").strip()),
                geo_engine.coordinate(str(data.get("picked_longitude") or "").strip(), limit=180),
            )
        except ValueError:
            return None

    def _clean_point(self, data: dict[str, Any]) -> None:
        """The point, as the map picker sends it (EVT.13): a promotion has none; a "Find
        coordinates" candidate is checked against the cached lookup of the address, so the
        request never sets a provider, a precision or a confidence."""
        if self.errors.keys() & {"latitude", "longitude"}:
            return
        if data.get("event_type") == EventPage.EventType.PROMOTION:
            data["latitude"] = data["longitude"] = None
        latitude, longitude = data.get("latitude"), data.get("longitude")
        if (latitude is None) != (longitude is None):
            self.add_error(
                "longitude" if latitude is not None else "latitude",
                _("Give both coordinates, or neither."),
            )
            return
        address = AddressQuery(**{name: str(data.get(name) or "") for name in ADDRESS_QUERY_FIELDS})
        try:
            self.picked = services.resolve_picked_point(
                self.page,
                latitude=latitude,
                longitude=longitude,
                source=str(data.get("geo_source") or ""),
                address=address,
                chosen=self._chosen_point(data),
            )
        except ValidationError as error:
            self.add_error(None, error)

    def _zone(self, data: dict[str, Any]) -> zoneinfo.ZoneInfo | None:
        name = data.get("timezone")
        if not name:
            return None
        try:
            return zoneinfo.ZoneInfo(name)
        except (zoneinfo.ZoneInfoNotFoundError, ValueError):
            self.add_error("timezone", _("Choose a time zone such as Europe/Rome."))
            return None

    def _rrule(self, data: dict[str, Any], all_day: bool, zone: zoneinfo.ZoneInfo | None) -> str:
        preset = data.get("recurrence", engine.PRESET_NONE)
        if preset == CUSTOM:
            return self.kept_rule
        if self.errors or zone is None:
            return ""
        try:
            return engine.build_rrule(
                preset,
                weekdays=data.get("recurrence_weekdays") or (),
                month_day=data.get("recurrence_month_day"),
                interval=data.get("recurrence_interval") or 1,
                ends=data.get("recurrence_ends") or engine.ENDS_NEVER,
                count=data.get("recurrence_count"),
                until=data.get("recurrence_until"),
                all_day=all_day,
                timezone=str(zone),
            )
        except engine.RecurrenceError as error:
            for item in error.errors:
                field, message = _RULE_ERRORS.get(item.part, ("recurrence", _("Check the rule.")))
                self.add_error(field, message)
            return ""

    def event_data(self) -> dict[str, Any]:
        """What the service gets: the cleaned fields in the shape of `EventInput`."""
        data = self.cleaned_data
        event: dict[str, Any] = {
            name: data[name] for name in ("title", "summary", "event_type", "timezone", "rrule")
        }
        all_day = bool(data["all_day"])
        event["all_day"] = all_day
        event["starts_at"] = None if all_day else data["starts_at"]
        event["ends_at"] = None if all_day else data["ends_at"]
        event["start_date"] = data["start_date"] if all_day else None
        event["end_date"] = data["end_date"] if all_day else None
        promotion = data["event_type"] == EventPage.EventType.PROMOTION
        for name in PLACE_FIELDS:
            event[name] = "" if promotion else data[name]
        event.update(self._point_data())
        event["products"] = [product.pk for product in data["products"]]
        event["categories"] = [category.pk for category in data["categories"]]
        return event

    def _point_data(self) -> dict[str, Any]:
        """The point fields to save: none clears the point and its metadata; a confirmed
        candidate brings its own metadata; otherwise the earlier metadata stays."""
        picked = self.picked
        data = self.cleaned_data
        if picked is None or not picked.source:
            return {
                "latitude": None,
                "longitude": None,
                "geo_source": "",
                "geo_provider": "",
                "geo_precision": "",
                "geo_confidence": None,
                "geo_at": None,
            }
        point: dict[str, Any] = {
            "latitude": data["latitude"],
            "longitude": data["longitude"],
            "geo_source": picked.source,
        }
        candidate: GeocodeResult | None = picked.candidate
        if candidate is not None:
            if picked.source == EventPage.GeoSource.GEOCODED:
                point["latitude"], point["longitude"] = candidate.latitude, candidate.longitude
            point.update(
                geo_provider=candidate.provider,
                geo_precision=candidate.precision,
                geo_confidence=candidate.confidence,
                geo_at=timezone.now(),
            )
        return point


class EventImageForm(forms.Form):
    """The upload form of the shop's image library: the file and its licence data (CMS.8).
    Never a collection: the image goes into the shop's own."""

    file = forms.FileField(
        label=_("File"),
        widget=forms.ClearableFileInput(
            attrs={"accept": "image/png,image/jpeg,image/webp,image/gif"}
        ),
        help_text=_(
            "PNG, JPEG, WebP or GIF (SVG is not accepted). The image is checked and saved again "
            "without its hidden data. Required."
        ),
    )
    title = forms.CharField(
        label=_("Title"),
        max_length=255,
        required=False,
        help_text=_("A name for the library, for example Our stand 2027. Optional."),
    )
    licence = forms.ModelChoiceField(
        queryset=ImageLicence.objects.order_by("name"),
        label=_("Licence"),
        empty_label=_("Choose a licence"),
        help_text=_(
            "Under which licence you may use the image. Your own photo is Own work. Required."
        ),
    )
    author = forms.CharField(
        label=_("Author"),
        max_length=200,
        help_text=_("Who made the picture or holds its rights. Required."),
    )
    source_url = forms.URLField(
        label=_("Source address"),
        required=False,
        max_length=500,
        help_text=_("Where the image comes from, starting with https://. Some licences need it."),
    )
    credit_text = forms.CharField(
        label=_("Credit line"),
        max_length=250,
        required=False,
        help_text=_("Optional: replaces the credit shown under the image."),
    )
    permission_ref = forms.CharField(
        label=_("Permission reference"),
        max_length=200,
        required=False,
        help_text=_("For a licence that needs a written permission: where it is kept."),
    )


def initial_from_page(page: EventPage) -> dict[str, Any]:
    """The form's initial values for the latest revision of `page` (a draft or a live page)."""
    try:
        zone = zoneinfo.ZoneInfo(page.timezone)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        zone = zoneinfo.ZoneInfo(default_timezone())

    def wall(value: dt.datetime | None) -> dt.datetime | None:
        return value.astimezone(zone).replace(tzinfo=None) if value is not None else None

    initial: dict[str, Any] = {
        name: getattr(page, name)
        for name in (
            "title",
            "summary",
            "event_type",
            "all_day",
            "start_date",
            "end_date",
            "timezone",
            *PLACE_FIELDS,
        )
    }
    initial["country"] = str(page.country) if page.country else ""
    initial["latitude"] = page.latitude
    initial["longitude"] = page.longitude
    initial["geo_source"] = page.geo_source
    initial["products"] = [product.pk for product in page.products.all()]
    initial["categories"] = [category.pk for category in page.categories.all()]
    initial["starts_at"] = wall(page.starts_at)
    initial["ends_at"] = wall(page.ends_at)
    choice = engine.preset_from_rrule(page.rrule, all_day=page.all_day, timezone=str(zone))
    if choice is None:
        initial["recurrence"] = CUSTOM
    else:
        initial.update(
            recurrence=choice.preset,
            recurrence_weekdays=list(choice.weekdays),
            recurrence_month_day=choice.month_day,
            recurrence_interval=choice.interval,
            recurrence_ends=choice.ends,
            recurrence_count=choice.count,
            recurrence_until=choice.until,
        )
    return initial
