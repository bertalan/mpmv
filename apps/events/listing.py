"""The public event pages (EVT.6, spec 15 "Public pages"): the list, the month view, the past
archive and the single-event ICS download.

`EventIndexPage` and `EventPage` call into this module from their routes. The reading is I/O
(the index's public events); the time rules (occurrences, the month grid, robots) are pure and
live in `engine`. A list shows every public event once (EVT.8): in the index's language when
the event has a translation there, otherwise in its original language, marked as such.
"""

import datetime as dt
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse
from django.urls import reverse
from django.utils import timezone, translation

from apps.catalog import slugs
from apps.cms import services as cms_services
from apps.core.i18n import configured_languages, primary_language
from apps.events import engine, services
from apps.events.panels import map_settings

if TYPE_CHECKING:
    from apps.events.models import EventIndexPage, EventPage

NOINDEX = "noindex, follow"
EVENT_TYPE_VALUES = frozenset(engine.EVENT_TYPES)


@dataclass(frozen=True)
class Item:
    """One event occurrence on a public page. `language` is the language code of an
    original-language fallback (the page is in another language than the list), else empty."""

    page: "EventPage"
    span: engine.Span
    language: str = ""

    @property
    def language_name(self) -> str:
        """The fallback's language in that language ("Deutsch"), for the visible label."""
        return (
            str(translation.get_language_info(self.language)["name_local"]) if self.language else ""
        )


@dataclass(frozen=True)
class Filters:
    """The GET filters of the list, the month view and the archive: all optional."""

    vendor: str = ""
    type: str = ""
    month: str = ""  # YYYY-MM
    translated_only: bool = False  # "Only events in <language>": real translations only

    @classmethod
    def parse(cls, query: Mapping[str, str]) -> "Filters":
        """Unknown values are ignored, not errors: the page then shows everything."""
        month = query.get("month", "")
        return cls(
            vendor=query.get("vendor", "")[:100],
            type=query.get("type", "") if query.get("type", "") in EVENT_TYPE_VALUES else "",
            month=month if engine.parse_month(month) else "",
            translated_only=query.get("translated", "") == "1",
        )

    def active(self) -> bool:
        return bool(self.vendor or self.type or self.month or self.translated_only)

    def query(self, *, month: bool = True, **extra: object) -> str:
        pairs = [("vendor", self.vendor), ("type", self.type)]
        if month:
            pairs.append(("month", self.month))
        if self.translated_only:
            pairs.append(("translated", "1"))
        pairs += [(key, str(value)) for key, value in extra.items()]
        return urlencode([(key, value) for key, value in pairs if value])


# --- Reading


def _fallback(event: services.LocalisedEvent) -> str:
    return event.language if event.fallback else ""


def _next_item(event: services.LocalisedEvent, now: dt.datetime) -> Item | None:
    spans = engine.event_spans(event.page.recurrence_spec(), now, engine.horizon_end(now))
    return Item(event.page, spans[0], _fallback(event)) if spans else None


def upcoming(events: list[services.LocalisedEvent], now: dt.datetime) -> list[Item]:
    """Each event once, at its next (or current) occurrence, soonest first."""
    items = [item for event in events if (item := _next_item(event, now)) is not None]
    return sorted(items, key=lambda item: (item.span.start, item.page.pk))


def past(events: list[services.LocalisedEvent], now: dt.datetime) -> list[Item]:
    """Events that ended, each at its last occurrence, newest first."""
    items: list[Item] = []
    for event in events:
        spec = event.page.recurrence_spec()
        if _next_item(event, now) is not None:
            continue
        first = engine.first_span(spec)
        window_start = max(first.start, now - dt.timedelta(days=30 * engine.HORIZON_MONTHS))
        spans = engine.event_spans(spec, window_start, now)
        if spans:
            items.append(Item(event.page, spans[-1], _fallback(event)))
    return sorted(items, key=lambda item: (item.span.end, item.page.pk), reverse=True)


def month_items(
    events: list[services.LocalisedEvent],
    year: int,
    month: int,
    *,
    not_before: dt.datetime | None = None,
) -> list[Item]:
    """Every occurrence that touches the month, by start."""
    items: list[Item] = []
    for event in events:
        start, end = engine.month_bounds(year, month, event.page.zone())
        if not_before is not None:
            start = max(start, not_before)
        items.extend(
            Item(event.page, span, _fallback(event))
            for span in engine.event_spans(event.page.recurrence_spec(), start, end)
        )
    return sorted(items, key=lambda item: (item.span.start, item.page.pk))


def apply_filters(items: list[Item], filters: Filters) -> list[Item]:
    """The vendor and type filters (the language filter is applied when the events are read)."""
    result = items
    if filters.vendor:
        result = [
            i for i in result if i.page.vendor is not None and i.page.vendor.slug == filters.vendor
        ]
    if filters.type:
        result = [i for i in result if i.page.event_type == filters.type]
    return result


@dataclass
class Options:
    vendors: list[tuple[str, str]] = field(default_factory=list)  # (slug, name)
    months: list[tuple[str, dt.date]] = field(default_factory=list)  # ("YYYY-MM", first day)


def filter_options(items: list[Item]) -> Options:
    """The vendors and months that actually have an upcoming event, so no choice is empty."""
    vendors = {
        item.page.vendor.slug: item.page.vendor.display_name
        for item in items
        if item.page.vendor is not None
    }
    months = {(item.span.start.year, item.span.start.month) for item in items}
    return Options(
        vendors=sorted(vendors.items(), key=lambda pair: pair[1].lower()),
        months=[(f"{y:04d}-{m:02d}", dt.date(y, m, 1)) for y, m in sorted(months)],
    )


# --- Rendering


def _page_number(request: HttpRequest) -> int:
    raw = request.GET.get("page", "1")
    if not raw.isascii() or not raw.isdigit() or int(raw) < 1:
        raise Http404("No such page")
    return int(raw)


def _paged(request: HttpRequest, items: list[Item], base: str, filters: Filters) -> dict[str, Any]:
    paginator = Paginator(items, engine.PAGE_SIZE)
    number = _page_number(request)
    if number > paginator.num_pages:
        raise Http404("No such page")
    current = paginator.page(number)

    def url(target: int) -> str:
        query = filters.query(page=target if target > 1 else "")
        return f"{base}?{query}" if query else base

    return {
        "items": list(current.object_list),
        "total": paginator.count,
        "page_number": number,
        "page_count": paginator.num_pages,
        "previous_url": url(number - 1) if number > 1 else "",
        "next_url": url(number + 1) if number < paginator.num_pages else "",
    }


def _groups(items: list[Item]) -> list[tuple[dt.date, list[Item]]]:
    grouped: dict[dt.date, list[Item]] = defaultdict(list)
    for item in items:
        grouped[item.span.start.date().replace(day=1)].append(item)
    return list(grouped.items())


def _common(index: "EventIndexPage", filters: Filters) -> dict[str, Any]:
    base = index.url or ""
    return {
        "filters": filters,
        "list_url": base,
        "past_url": base + "past/",
        "calendar_url": f"{base}calendar/{timezone.localdate():%Y-%m}/",
        "event_types": list(EVENT_TYPE_CHOICES()),
        "language_name": str(
            translation.get_language_info(index.locale.language_code)["name_translated"]
        ),
    }


def EVENT_TYPE_CHOICES() -> list[tuple[str, str]]:
    from apps.events.models import EventType

    return [(str(value), str(label)) for value, label in EventType.choices]


def _events(index: "EventIndexPage", filters: Filters) -> list[services.LocalisedEvent]:
    """Every public event of the index's language, with the language filter only: the vendor and
    type filters are applied to the items, so the filter choices keep listing every vendor."""
    wide = replace(filters, vendor="", type="")
    return services.events_for_locale(index.locale, wide, site=index.get_site())


def render_list(index: "EventIndexPage", request: HttpRequest) -> HttpResponse:
    now = timezone.now()
    filters = Filters.parse(request.GET)
    events = _events(index, filters)
    if filters.month:
        year, month = engine.parse_month(filters.month) or (now.year, now.month)
        every = month_items(events, year, month, not_before=now)
    else:
        every = upcoming(events, now)
    options = filter_options(upcoming(events, now))
    shown = apply_filters(every, filters)
    paged = _paged(request, shown, index.url or "", filters)
    plotted = sum(1 for item in paged["items"] if item.page.latitude is not None)
    context = {
        **_common(index, filters),
        **paged,
        "plotted": plotted,
        "map": map_settings() if plotted else None,
        "groups": _groups(paged["items"]),
        "options": options,
        "robots": NOINDEX if filters.active() or paged["page_number"] > 1 else "",
    }
    response: HttpResponse = index.render(request, context_overrides=context)
    return response


def render_past(index: "EventIndexPage", request: HttpRequest) -> HttpResponse:
    now = timezone.now()
    filters = Filters.parse(request.GET)
    items = apply_filters(past(_events(index, filters), now), filters)
    paged = _paged(request, items, (index.url or "") + "past/", filters)
    context = {
        **_common(index, filters),
        **paged,
        "groups": _groups(paged["items"]),
        "robots": NOINDEX,  # the archive is for people; the live list is the indexed page
    }
    response: HttpResponse = index.render(
        request, context_overrides=context, template="events/event_past.html"
    )
    return response


@dataclass(frozen=True)
class Cell:
    day: dt.date
    in_month: bool
    items: list[Item]


def render_calendar(
    index: "EventIndexPage", request: HttpRequest, year: int, month: int
) -> HttpResponse:
    if engine.parse_month(f"{year:04d}-{month:02d}") is None:
        raise Http404("No such month")
    filters = Filters.parse(request.GET)
    items = apply_filters(month_items(_events(index, filters), year, month), filters)
    by_day: dict[dt.date, list[Item]] = defaultdict(list)
    for item in items:
        for day in item.span.days():
            by_day[day].append(item)
    weeks = [
        [Cell(day, day.month == month, by_day.get(day, [])) for day in week]
        for week in engine.month_grid(year, month)
    ]
    base = (index.url or "") + "calendar/"
    previous = engine.neighbour_month(year, month, -1)
    following = engine.neighbour_month(year, month, 1)

    def month_url(target: tuple[int, int]) -> str:
        query = filters.query(month=False)
        return f"{base}{target[0]:04d}-{target[1]:02d}/" + (f"?{query}" if query else "")

    context = {
        **_common(index, filters),
        "month_start": dt.date(year, month, 1),
        "weeks": weeks,
        "weekdays": weeks[0] if weeks else [],
        "items": items,
        "previous_url": month_url(previous) if previous[0] >= 1900 else "",
        "next_url": month_url(following) if following[0] <= 2200 else "",
        "robots": NOINDEX,  # endless months: only the live list is indexed
    }
    response: HttpResponse = index.render(
        request, context_overrides=context, template="events/event_calendar.html"
    )
    return response


# --- One event


EVENT_MAP_ZOOM = 15


def address_lines(page: "EventPage") -> list[str]:
    """The street, the postcode and city, and the country: the address as text lines."""
    place = " ".join(part for part in (page.postcode, page.city) if part)
    country = str(page.country or "")
    return [line for line in (page.street, place, country) if line]


def map_context(page: "EventPage") -> dict[str, Any]:
    """The map and the map links of an event page (EVT.15, spec 15 "Maps").

    With both coordinates: the map container's values and the three directions links. With an
    address only: a plain "Open in maps" search link. With neither: nothing.
    """
    lines = address_lines(page)
    if page.latitude is not None and page.longitude is not None:
        return {
            "point": {
                "lat": engine.plain_coordinate(page.latitude),
                "lng": engine.plain_coordinate(page.longitude),
                "zoom": EVENT_MAP_ZOOM,
                "label": page.venue_name or page.title,
                "address": ", ".join(lines),
            },
            "directions": engine.directions_links(page.latitude, page.longitude),
            "address_lines": lines,
            "search_url": None,
            **map_settings(),
        }
    return {
        "point": None,
        "directions": None,
        "search_url": engine.search_url(lines or [page.venue_name]),
    }


def event_context(page: "EventPage", request: HttpRequest) -> dict[str, Any]:
    """What the event template needs beyond the page: robots, links to the ICS and the report,
    and the map."""
    event_url = page.get_full_url(request) or ""
    vendor = page.vendor
    return {
        **_language_links(page, request),
        "map": map_context(page),
        "robots": NOINDEX if page.is_noindex() else "",
        "event_url": event_url,
        "ics_url": f"{url}{engine.ICS_PATH}" if (url := page.get_url(request)) else "",
        "report_url": reverse("dsa_notice") + "?" + urlencode({"kind": "event", "url": event_url}),
        "vendor_href": (
            slugs.vendor_path(
                page.locale.language_code, vendor.slug, default_language=primary_language()
            )
            if vendor is not None
            else ""
        ),
    }


def _language_links(page: "EventPage", request: HttpRequest) -> dict[str, Any]:
    """The canonical URL and the `hreflang` alternates of an event page (`{% seo_links %}`):
    only translations that exist and are published, `x-default` on the EN translation or else the
    original (spec 15 "One URL per existing translation"). Nothing in a preview."""
    site = page.get_site()
    if site is None or getattr(request, "is_preview", False):
        return {}

    def absolute(url: str) -> str:
        return url if "://" in url else site.root_url + url

    own = page.get_url(request)
    found = {
        code: absolute(url) for code, url in cms_services.page_language_urls(page, request).items()
    }
    alternates = engine.event_hreflang(
        found, page.original_locale.language_code, configured_languages()
    )
    return {"canonical_url": absolute(own) if own else "", "alternates": alternates}
