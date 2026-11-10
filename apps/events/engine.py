"""Pure rules of the events module (spec 15). No Django, no ORM (ARC.18).

EVT.1 holds the status derivation and the RRULE check the model needs; EVT.2 adds the RRULE
subset validator and the expansion of occurrences here; EVT.3 the moderation threshold, the ICS
SEQUENCE rule, the vendor-safe body rules and the slugs.
"""

import calendar
import re
import unicodedata
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

# Event types (spec 15 "Page models"). There is deliberately no closure type: a vendor's
# holiday or shop closure is a vendor setting (owner decision Q-73, VEN.20).
PROMOTION = "promotion"
FAIR = "fair"  # a trade fair or a meeting
OTHER = "other"
EVENT_TYPES = (PROMOTION, FAIR, OTHER)

# The denormalised `status` column (spec 15 "Status").
DRAFT = "draft"
PENDING = "pending"
PUBLISHED = "published"
CANCELLED = "cancelled"
HIDDEN = "hidden"
STATUSES = (DRAFT, PENDING, PUBLISHED, CANCELLED, HIDDEN)

_RULE_PART = re.compile(r"(?:^|;)\s*(COUNT|UNTIL)\s*=", re.IGNORECASE)


def event_status(*, live: bool, in_workflow: bool, cancelled: bool, hidden: bool) -> str:
    """The `status` of an event page from its Wagtail state.

    `hidden`: unpublished by staff (`hidden_at` set). `cancelled`: `cancelled_at` set; a live
    cancelled page stays online with a "Cancelled" message. A workflow in progress makes a page
    that is not live `pending`; an edit of a live page in review keeps it `published`. A page
    that is not live and not in review is a `draft`, also after it was unpublished.
    """
    if hidden:
        return HIDDEN
    if live:
        return CANCELLED if cancelled else PUBLISHED
    return PENDING if in_workflow else DRAFT


def has_count_and_until(rrule: str) -> bool:
    """Whether an RRULE names both COUNT and UNTIL, which RFC 5545 forbids (§3.3.10)."""
    parts = {match.upper() for match in _RULE_PART.findall(rrule)}
    return parts == {"COUNT", "UNTIL"}


# --- Recurrence: an RFC 5545 subset (spec 15 "Recurrence", EVT.2) ---------------------------
#
# The subset is small enough to expand directly with `datetime` and `zoneinfo`, so the engine
# needs no rule library. Occurrences are computed in the local time of the event's zone and
# only then turned into instants, so a weekly 10:00 meeting stays at 10:00 local across
# daylight saving changes.

FREQUENCIES = ("DAILY", "WEEKLY", "MONTHLY", "YEARLY")
WEEKDAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")  # index = date.weekday()
ALLOWED_PARTS = ("FREQ", "INTERVAL", "COUNT", "UNTIL", "BYDAY", "BYMONTHDAY", "BYMONTH", "WKST")
MAX_INTERVAL = 12
HORIZON_MONTHS = 18  # listings and the month view expand up to 18 months ahead
DEFAULT_LIMIT = 500  # caps runaway rules

# ASCII digits only: `int()` and Unicode `\d` would also take "1_0", "+3" and non-ASCII digits.
_BYDAY = re.compile(r"^([+-]?[0-9]{1,2})?(MO|TU|WE|TH|FR|SA|SU)$")
_UNTIL_UTC = re.compile(r"^([0-9]{4})([0-9]{2})([0-9]{2})T([0-9]{2})([0-9]{2})([0-9]{2})Z$")
_UNTIL_DATE = re.compile(r"^([0-9]{4})([0-9]{2})([0-9]{2})$")
_UNSIGNED = re.compile(r"^[0-9]+$")  # INTERVAL, COUNT
_DAY_OR_MONTH = re.compile(r"^[0-9]{1,2}$")  # BYMONTH
_SIGNED_DAY = re.compile(r"^[+-]?[0-9]{1,2}$")  # BYMONTHDAY (RFC 5545 monthdaynum)

ByDay = tuple[tuple[int | None, int], ...]  # (ordinal or None, weekday with Monday = 0)


@dataclass(frozen=True)
class RuleError:
    """One refused part of an RRULE; `part` names it (FREQ, BYSETPOS, ...) for a field error."""

    part: str
    message: str


class RecurrenceError(ValueError):
    """An RRULE outside the supported subset. `errors` lists every refused part."""

    def __init__(self, errors: Sequence[RuleError]) -> None:
        self.errors = tuple(errors)
        super().__init__("; ".join(f"{e.part}: {e.message}" for e in self.errors))


@dataclass(frozen=True)
class RRuleSubset:
    """A validated RRULE in the supported subset."""

    freq: str
    interval: int = 1
    count: int | None = None
    until: datetime | date | None = None  # an aware UTC datetime for timed events, else a date
    byday: ByDay = ()
    bymonthday: tuple[int, ...] = ()
    bymonth: tuple[int, ...] = ()
    wkst: int = 0


@dataclass(frozen=True)
class RecurrenceSpec:
    """The recurrence fields of an event page, free of the ORM (ARC.18).

    Timed events set `starts_at` and `ends_at` (aware) and may exclude `exdates` (instants of
    occurrence starts); all-day events set `start_date` and the inclusive `end_date` and may
    exclude `exdate_days` (first days of occurrences).
    """

    all_day: bool
    timezone: str
    rrule: str = ""
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    start_date: date | None = None
    end_date: date | None = None
    exdates: tuple[datetime, ...] = ()
    exdate_days: tuple[date, ...] = ()


@dataclass(frozen=True)
class Occurrence:
    """One occurrence: aware datetimes in the event's zone, or dates with an inclusive end."""

    start: datetime | date
    end: datetime | date


# --- Validation ------------------------------------------------------------------------------


def _parse_int(value: str, pattern: re.Pattern[str] = _UNSIGNED) -> int | None:
    """`value` as an int when it matches `pattern` (ASCII digits), else None."""
    return int(value) if pattern.match(value) else None


def _parse_numbers(
    part: str, value: str, high: int, errors: list[RuleError], *, last: bool
) -> tuple[int, ...]:
    """A comma list of 1..high, plus -1 (the last day) when `last` is set (and then signed)."""
    pattern = _SIGNED_DAY if last else _DAY_OR_MONTH
    numbers: list[int] = []
    for item in value.split(","):
        number = _parse_int(item, pattern)
        if number is None or not (1 <= number <= high or (last and number == -1)):
            errors.append(RuleError(part, f"{item!r} is not allowed."))
        else:
            numbers.append(number)
    return tuple(numbers)


def _parse_until(value: str, all_day: bool, errors: list[RuleError]) -> datetime | date | None:
    match = (_UNTIL_DATE if all_day else _UNTIL_UTC).match(value)
    if match is None:
        kind = "a date (YYYYMMDD)" if all_day else "a UTC timestamp (YYYYMMDDTHHMMSSZ)"
        errors.append(RuleError("UNTIL", f"must be {kind} for this event."))
        return None
    numbers = [int(group) for group in match.groups()]
    try:
        if all_day:
            return date(numbers[0], numbers[1], numbers[2])
        year, month, day, hour, minute, second = numbers
        return datetime(year, month, day, hour, minute, second, tzinfo=UTC)
    except ValueError:
        errors.append(RuleError("UNTIL", f"{value!r} is not a valid date."))
        return None


def _parse_byday(value: str, freq: str, errors: list[RuleError]) -> ByDay:
    days: list[tuple[int | None, int]] = []
    highest = 53 if freq == "YEARLY" else 5
    for item in value.split(","):
        match = _BYDAY.match(item)
        if match is None:
            errors.append(RuleError("BYDAY", f"{item!r} is not a weekday."))
            continue
        ordinal = int(match.group(1)) if match.group(1) else None
        if ordinal is not None and freq not in ("MONTHLY", "YEARLY"):
            errors.append(RuleError("BYDAY", "an ordinal needs FREQ=MONTHLY or FREQ=YEARLY."))
        elif ordinal is not None and not 1 <= abs(ordinal) <= highest:
            errors.append(RuleError("BYDAY", f"{item!r} has an ordinal out of range."))
        else:
            days.append((ordinal, WEEKDAYS.index(match.group(2))))
    return tuple(days)


def _split_parts(text: str, errors: list[RuleError]) -> dict[str, str]:
    body = text.strip()
    if body.upper().startswith("RRULE:"):
        body = body[len("RRULE:") :]
    parts: dict[str, str] = {}
    for chunk in body.split(";"):
        if not chunk.strip():
            continue
        name, sep, value = chunk.partition("=")
        name, value = name.strip().upper(), value.strip().upper()
        if name in parts:
            errors.append(RuleError(name, "appears more than once."))
        elif name not in ALLOWED_PARTS:
            # BYSETPOS, BYHOUR, RDATE and the rest: refused with a field error (case 8).
            errors.append(RuleError(name, "is not supported."))
        elif not sep or not value:
            errors.append(RuleError(name, "has no value."))
        else:
            parts[name] = value
    return parts


def validate_rrule(text: str, all_day: bool) -> RRuleSubset:
    """Parse an RRULE in the supported subset, or raise `RecurrenceError` naming every bad part."""
    errors: list[RuleError] = []
    parts = _split_parts(text, errors)

    freq = parts.get("FREQ", "")
    if not freq:
        if not any(e.part == "FREQ" for e in errors):
            errors.append(RuleError("FREQ", "is required."))
    elif freq not in FREQUENCIES:
        errors.append(RuleError("FREQ", f"{freq} is not supported; use {', '.join(FREQUENCIES)}."))

    interval = 1
    if "INTERVAL" in parts:
        number = _parse_int(parts["INTERVAL"])
        if number is None or not 1 <= number <= MAX_INTERVAL:
            errors.append(RuleError("INTERVAL", f"must be between 1 and {MAX_INTERVAL}."))
        else:
            interval = number

    count = None
    if "COUNT" in parts and "UNTIL" in parts:
        errors.append(RuleError("COUNT", "COUNT and UNTIL cannot be used together."))
    if "COUNT" in parts:
        count = _parse_int(parts["COUNT"])
        if count is None or count < 1:
            errors.append(RuleError("COUNT", "must be a positive number."))
            count = None
    until = _parse_until(parts["UNTIL"], all_day, errors) if "UNTIL" in parts else None

    byday = _parse_byday(parts["BYDAY"], freq, errors) if "BYDAY" in parts else ()
    if "BYMONTHDAY" in parts and freq == "WEEKLY":
        errors.append(RuleError("BYMONTHDAY", "is not allowed with FREQ=WEEKLY."))
    bymonthday = (
        _parse_numbers("BYMONTHDAY", parts["BYMONTHDAY"], 31, errors, last=True)
        if "BYMONTHDAY" in parts
        else ()
    )
    bymonth = (
        _parse_numbers("BYMONTH", parts["BYMONTH"], 12, errors, last=False)
        if "BYMONTH" in parts
        else ()
    )
    wkst = 0
    if "WKST" in parts:
        if parts["WKST"] in WEEKDAYS:
            wkst = WEEKDAYS.index(parts["WKST"])
        else:
            errors.append(RuleError("WKST", f"{parts['WKST']!r} is not a weekday."))

    if errors:
        raise RecurrenceError(errors)
    return RRuleSubset(freq, interval, count, until, byday, bymonthday, bymonth, wkst)


# --- Candidate days --------------------------------------------------------------------------


def _add_months(year: int, month: int, months: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + months
    return index // 12, index % 12 + 1


def horizon_end(now: datetime) -> datetime:
    """The end of the expansion horizon: HORIZON_MONTHS after `now`, the day clamped."""
    year, month = _add_months(now.year, now.month, HORIZON_MONTHS)
    day = min(now.day, calendar.monthrange(year, month)[1])
    return now.replace(year=year, month=month, day=day)


def _is_monthday(day: date, bymonthday: tuple[int, ...]) -> bool:
    length = calendar.monthrange(day.year, day.month)[1]
    return day.day in bymonthday or (-1 in bymonthday and day.day == length)


def _matches_weekday(day: date, byday: ByDay, *, in_year: bool) -> bool:
    """Whether `day` matches BYDAY; an ordinal counts within its month, or its year."""
    if in_year:
        position = day.timetuple().tm_yday
        length = 366 if calendar.isleap(day.year) else 365
    else:
        position = day.day
        length = calendar.monthrange(day.year, day.month)[1]
    forward = (position - 1) // 7 + 1
    backward = -((length - position) // 7 + 1)
    return any(
        day.weekday() == weekday and ordinal in (None, forward, backward)
        for ordinal, weekday in byday
    )


def _month_days(year: int, month: int, rule: RRuleSubset, anchor: date) -> list[date]:
    """Candidate days in one month: BYMONTHDAY and BYDAY, or the anchor's day."""
    length = calendar.monthrange(year, month)[1]
    if not rule.bymonthday and not rule.byday:
        return [date(year, month, anchor.day)] if anchor.day <= length else []
    days = [date(year, month, d) for d in range(1, length + 1)]
    if rule.bymonthday:
        days = [d for d in days if _is_monthday(d, rule.bymonthday)]
    if rule.byday:
        days = [d for d in days if _matches_weekday(d, rule.byday, in_year=False)]
    return days


def _period_start(rule: RRuleSubset, anchor: date, step: int) -> date:
    """The first day of the period `step` periods after the anchor's."""
    if rule.freq == "DAILY":
        return anchor + timedelta(days=step)
    if rule.freq == "WEEKLY":
        week_start = anchor - timedelta(days=(anchor.weekday() - rule.wkst) % 7)
        return week_start + timedelta(weeks=step)
    if rule.freq == "MONTHLY":
        year, month = _add_months(anchor.year, anchor.month, step)
        return date(year, month, 1)
    return date(anchor.year + step, 1, 1)


def _period_days(rule: RRuleSubset, anchor: date, step: int) -> list[date]:
    """The candidate days of one period, in order."""
    start = _period_start(rule, anchor, step)
    if rule.freq == "DAILY":
        days = [start]
    elif rule.freq == "WEEKLY":
        weekdays = {weekday for _, weekday in rule.byday} or {anchor.weekday()}
        days = [start + timedelta(days=i) for i in range(7)]
        days = [d for d in days if d.weekday() in weekdays]
    elif rule.freq == "MONTHLY":
        return (
            _month_days(start.year, start.month, rule, anchor)
            if (not rule.bymonth or start.month in rule.bymonth)
            else []
        )
    elif rule.bymonth:
        return [d for m in sorted(rule.bymonth) for d in _month_days(start.year, m, rule, anchor)]
    elif rule.bymonthday or rule.byday:
        # YEARLY without BYMONTH: the whole year; BYDAY ordinals count within the year.
        length = 366 if calendar.isleap(start.year) else 365
        days = [start + timedelta(days=i) for i in range(length)]
        if rule.bymonthday:
            days = [d for d in days if _is_monthday(d, rule.bymonthday)]
        if rule.byday:
            days = [d for d in days if _matches_weekday(d, rule.byday, in_year=True)]
        return days
    else:
        return _month_days(start.year, anchor.month, rule, anchor)
    # DAILY and WEEKLY: BYDAY, BYMONTH and BYMONTHDAY limit the days.
    if rule.freq == "DAILY" and rule.byday:
        days = [d for d in days if _matches_weekday(d, rule.byday, in_year=False)]
    if rule.bymonth:
        days = [d for d in days if d.month in rule.bymonth]
    if rule.bymonthday:
        days = [d for d in days if _is_monthday(d, rule.bymonthday)]
    return days


def _rule_days(rule: RRuleSubset | None, anchor: date, last_day: date) -> list[date]:
    """The days of the recurrence set up to `last_day`: DTSTART first, then the rule's days."""
    days = [anchor]
    if rule is None:
        return days
    step = 0
    while _period_start(rule, anchor, step) <= last_day:
        days.extend(d for d in _period_days(rule, anchor, step) if anchor < d <= last_day)
        if rule.count is not None and len(days) >= rule.count:
            return days[: rule.count]
        step += rule.interval
    return days


# --- Expansion -------------------------------------------------------------------------------


def _resolve_local(naive: datetime, zone: ZoneInfo) -> datetime:
    """A local wall time as an aware datetime (RFC 5545 §3.3.5).

    An ambiguous time (fall back) takes the first instance (fold=0). A time that does not exist
    (spring forward) moves forward by the gap: fold=0 reads it with the offset in force before
    the gap, and the round trip through UTC lands on the shifted wall time.
    """
    aware = naive.replace(tzinfo=zone, fold=0)
    return aware.astimezone(UTC).astimezone(zone)


def expand(
    spec: RecurrenceSpec,
    window_start: datetime,
    window_end: datetime,
    limit: int = DEFAULT_LIMIT,
) -> list[Occurrence]:
    """The occurrences of an event that overlap [window_start, window_end), in start order.

    DTSTART is always the first occurrence and counts towards COUNT, even when UNTIL is before
    it (UNTIL bounds only the later occurrences); EXDATEs are removed after
    COUNT and UNTIL apply (RFC 5545 §3.8.5.1). The window is capped at HORIZON_MONTHS after its
    start, and the result at `limit` occurrences. Every occurrence lasts as long as the first:
    the same elapsed time for timed events, the same number of days for all-day events.
    """
    if window_start.tzinfo is None or window_end.tzinfo is None:
        raise ValueError("expand needs aware window datetimes")
    if limit < 1:
        raise ValueError("limit must be at least 1")
    window_end = min(window_end, horizon_end(window_start))
    zone = ZoneInfo(spec.timezone)
    rule = validate_rrule(spec.rrule, spec.all_day) if spec.rrule.strip() else None
    if spec.all_day:
        return _expand_days(spec, rule, zone, window_start, window_end, limit)
    return _expand_timed(spec, rule, zone, window_start, window_end, limit)


def _expand_days(
    spec: RecurrenceSpec,
    rule: RRuleSubset | None,
    zone: ZoneInfo,
    window_start: datetime,
    window_end: datetime,
    limit: int,
) -> list[Occurrence]:
    if spec.start_date is None or spec.end_date is None:
        raise ValueError("an all-day event needs start_date and end_date")
    span = spec.end_date - spec.start_date
    # An all-day occurrence covers its local days, midnight to midnight in the event's zone.
    first_day = window_start.astimezone(zone).date() - span
    local_end = window_end.astimezone(zone)
    last_day = local_end.date() - (timedelta(0) if local_end.time() != time(0) else timedelta(1))
    excluded = set(spec.exdate_days)
    result: list[Occurrence] = []
    for day in _rule_days(rule, spec.start_date, last_day):
        # DTSTART is always the first occurrence; UNTIL bounds only the later ones.
        until = rule.until if rule is not None else None
        if isinstance(until, date) and day > until and day != spec.start_date:
            break
        if day >= first_day and day not in excluded:
            result.append(Occurrence(day, day + span))
            if len(result) >= limit:
                break
    return result


def _expand_timed(
    spec: RecurrenceSpec,
    rule: RRuleSubset | None,
    zone: ZoneInfo,
    window_start: datetime,
    window_end: datetime,
    limit: int,
) -> list[Occurrence]:
    if spec.starts_at is None or spec.ends_at is None:
        raise ValueError("a timed event needs starts_at and ends_at")
    first = spec.starts_at.astimezone(zone)
    wall_time = first.time().replace(tzinfo=None)
    duration = spec.ends_at - spec.starts_at
    excluded = {instant.astimezone(UTC) for instant in spec.exdates}
    result: list[Occurrence] = []
    for day in _rule_days(rule, first.date(), window_end.astimezone(zone).date()):
        local = datetime.combine(day, wall_time)
        start = first if day == first.date() else _resolve_local(local, zone)
        # DTSTART is always the first occurrence; UNTIL bounds only the later ones.
        until = rule.until if rule is not None else None
        if isinstance(until, datetime) and start > until and day != first.date():
            break
        if start >= window_end:
            break
        end = (start.astimezone(UTC) + duration).astimezone(zone)
        if end > window_start and start.astimezone(UTC) not in excluded:
            result.append(Occurrence(start, end))
            if len(result) >= limit:
                break
    return result


# --- Moderation (spec 15 "Moderation with Wagtail workflows", EVT.3) -------------------------

# `VendorEventPolicy.mode`.
DEFAULT_MODE = "default"
ALWAYS_REVIEW = "always_review"
TRUSTED = "trusted"
POLICY_MODES = (DEFAULT_MODE, ALWAYS_REVIEW, TRUSTED)


def needs_review(approved_count: int, threshold: int, mode: str, *, is_staff: bool) -> bool:
    """Whether a submission goes through "Event review".

    Staff and platform events (`is_staff`): never. `always_review`: always. `trusted`: never.
    Otherwise while the vendor has fewer than `threshold` approved events.
    """
    if mode not in POLICY_MODES:
        raise ValueError(f"unknown moderation mode {mode!r}")
    if is_staff or mode == TRUSTED:
        return False
    if mode == ALWAYS_REVIEW:
        return True
    return approved_count < threshold


# --- ICS SEQUENCE (spec 15 "Services") --------------------------------------------------------

# The fields the ICS shows: a published change of any of them bumps `sequence`. `cancelled_at`
# stands for the status (STATUS:CANCELLED).
ICS_FIELDS = (
    "title",
    "summary",
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
    "cancelled_at",
)


def ics_changed(before: Mapping[str, object], after: Mapping[str, object]) -> bool:
    return any(before.get(name) != after.get(name) for name in ICS_FIELDS)


def next_sequence(
    sequence: int,
    before: Mapping[str, object],
    after: Mapping[str, object],
    *,
    was_published: bool,
) -> int:
    """The ICS SEQUENCE after publishing `after` over the stored `before`.

    Up by one when a field the ICS shows changed on an event that calendars may already hold
    (`was_published`); unchanged on a no-op publish and on the first publication.
    """
    if was_published and ics_changed(before, after):
        return sequence + 1
    return sequence


# --- Vendor-safe body (spec 15 "How vendors author an `EventPage`") ----------------------------

# The CMS.10 product grid and the CMS.9 countdown join when those tasks add the blocks.
VENDOR_SAFE_BLOCKS = frozenset({"paragraph", "figure", "gallery", "cta", "faq"})

_HREF = re.compile(r"""\b(?:href|src)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.IGNORECASE)
_ENTITY = re.compile(r"&#(x[0-9a-f]+|[0-9]+);?", re.IGNORECASE)
_UNSAFE_SCHEMES = ("javascript:", "data:", "vbscript:")


def refused_blocks(block_types: Sequence[str], *, is_staff: bool) -> list[str]:
    """The block types a vendor may not use, in order; staff get every block."""
    if is_staff:
        return []
    return [name for name in block_types if name not in VENDOR_SAFE_BLOCKS]


def _decode_entity(match: re.Match[str]) -> str:
    code = match.group(1)
    number = int(code[1:], 16) if code[0] in "xX" else int(code)
    return chr(number) if number < 0x110000 else ""


def _link_scheme(url: str) -> str:
    text = _ENTITY.sub(_decode_entity, url)
    # Browsers drop control characters and spaces inside a scheme ("java\tscript:").
    return "".join(ch for ch in text if ch > " ").lower()


def has_unsafe_link(html: str) -> bool:
    """Whether rich text links to a `javascript:`, `data:` or `vbscript:` URL."""
    for match in _HREF.finditer(html):
        url = next(group for group in match.groups() if group is not None)
        if _link_scheme(url).startswith(_UNSAFE_SCHEMES):
            return True
    return False


# Size limits of vendor-authored content (a stored revision is re-parsed on every read).
MAX_BODY_BLOCKS = 50
MAX_EXDATES = 500  # excluded occurrences or days
MAX_LIST_ITEMS = 50  # images in a gallery, questions in an FAQ
MAX_TEXT_LENGTH = 20_000  # characters in one text value of a block
MAX_DEPTH = 8  # nesting of one block value (a gallery item is 4 deep)

_FOREIGN_REFERENCE = re.compile(
    r"""<embed\b|\blinktype\s*=\s*["']?(?:document|image)\b""", re.IGNORECASE
)


def vendor_collection_name(vendor_public_id: str) -> str:
    """The name of a vendor's own Wagtail image collection (a child of the root collection);
    the vendor-area upload stores there and the services accept only images from it."""
    return f"vendor-{vendor_public_id}"


def has_foreign_reference(html: str) -> bool:
    """Whether vendor rich text embeds media or links a document by id: neither is a vendor
    feature, and an id could name another tenant's file."""
    return _FOREIGN_REFERENCE.search(html) is not None


def image_ids(raw: Any) -> list[Any]:
    """Every image reference of the `figure` and `gallery` blocks of a raw body (a gallery
    item is either the plain value or Wagtail's `{"type": "item", "value": ...}` wrapper)."""
    found: list[Any] = []

    def walk(node: Any) -> None:
        if isinstance(node, Mapping):
            if "image" in node:
                found.append(node["image"])
            for child in node.values():
                walk(child)
        elif isinstance(node, list | tuple):
            for child in node:
                walk(child)

    for block in raw:
        if block.get("type") in ("figure", "gallery"):
            walk(block.get("value"))
    return found


def cta_links(raw: Any) -> list[Mapping[str, Any]]:
    """The `link` values of the `cta` blocks of a raw body."""
    links: list[Mapping[str, Any]] = []
    for block in raw:
        value = block.get("value")
        if block.get("type") == "cta" and isinstance(value, Mapping):
            link = value.get("link")
            if isinstance(link, Mapping):
                links.append(link)
    return links


def cta_url_problem(url: str) -> str | None:
    """Why a vendor CTA may not use `url`, or None: only `https://` addresses (spec 15)."""
    text = url.strip()
    if not text.lower().startswith("https://") or len(text) <= len("https://"):
        return "A call-to-action link must start with https://."
    if any(ch <= " " for ch in text):
        return "A call-to-action link must not contain spaces."
    authority = text[len("https://") :].split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if "@" in authority or "\\" in text:
        return "A call-to-action link must not contain a user name."
    return None


def body_limit_problem(raw: Any) -> str | None:
    """Why a raw body is too big, or None: blocks, list items and text length are capped."""
    if len(raw) > MAX_BODY_BLOCKS:
        return f"A body has at most {MAX_BODY_BLOCKS} blocks."

    def too_big(node: Any, depth: int) -> str | None:
        if depth > MAX_DEPTH:
            return "A block is nested too deeply."
        if isinstance(node, str):
            return "A text is too long." if len(node) > MAX_TEXT_LENGTH else None
        if isinstance(node, Mapping):
            return next((p for child in node.values() if (p := too_big(child, depth + 1))), None)
        if isinstance(node, list | tuple):
            if len(node) > MAX_LIST_ITEMS:
                return f"A list has at most {MAX_LIST_ITEMS} items."
            return next((p for child in node if (p := too_big(child, depth + 1))), None)
        return None

    return next((p for block in raw if (p := too_big(block.get("value"), 1))), None)


# --- Slugs (spec 15 "Slugs and routes") --------------------------------------------------------

# The route names of `EventIndexPage`: an event never takes one as its slug.
RESERVED_SLUGS = frozenset({"calendar", "past", "vendor", "type", "feed.rss", "calendar.ics"})
DEFAULT_SLUG = "event"
_NOT_SLUG = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return _NOT_SLUG.sub("-", ascii_text.lower()).strip("-")[:200].strip("-")


def event_slug(title: str, *, taken: Set[str]) -> str:
    """A slug from `title` that is neither taken among its siblings nor an index route name."""
    base = _slugify(title) or DEFAULT_SLUG
    if base not in taken and base not in RESERVED_SLUGS:
        return base
    number = 2
    while f"{base}-{number}" in taken:
        number += 1
    return f"{base}-{number}"
