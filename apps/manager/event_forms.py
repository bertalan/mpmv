"""The vendor's event form (EVT.4): a thin validator, the services own the rules.

Times are typed as the wall clock of the event's time zone (no browser zone is involved), the
recurrence presets of `apps.events.engine` write the RRULE, and a promotion has no place.
"""

import datetime as dt
import zoneinfo
from typing import Any

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.events import engine
from apps.events.models import EventPage, default_timezone

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
_DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


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
    )

    def __init__(self, *args: Any, kept_rule: str = "", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.kept_rule = kept_rule  # a rule the presets cannot write, kept by "custom"
        if kept_rule:
            self.fields["recurrence"].choices = [  # type: ignore[attr-defined]
                *self.fields["recurrence"].choices,  # type: ignore[attr-defined]
                (CUSTOM, _("Keep the rule already saved: %(rule)s") % {"rule": kept_rule}),
            ]
        self.fields["timezone"].initial = default_timezone()

    def sections(self) -> list[tuple[Any, list[forms.BoundField]]]:
        return [(title, [self[name] for name in names]) for title, names in self.SECTIONS]

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
        return data

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
        return event


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
