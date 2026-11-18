"""The `Event` node of an event page's graph (EVT.10, spec 15 "Structured data"), pure.

Plain facts in, one schema.org `Event` out; no ORM. The adapter in `page_builders` reads the
page. An empty value is left out, never defaulted.
"""

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

SCHEMA_URL: Final = "https://schema.org"
ATTENDANCE_MODES: Final = {
    "in_person": "OfflineEventAttendanceMode",
    "online": "OnlineEventAttendanceMode",
    "mixed": "MixedEventAttendanceMode",
}


@dataclass(frozen=True)
class EventFacts:
    url: str
    name: str
    description: str
    start: dt.datetime | dt.date
    end: dt.datetime | dt.date
    language: str
    attendance: str  # "in_person", "online" or "mixed"
    cancelled: bool = False
    venue_name: str = ""
    street: str = ""
    postcode: str = ""
    city: str = ""
    region: str = ""
    country: str = ""
    latitude: float | None = None
    longitude: float | None = None
    online_url: str = ""
    image: str = ""
    organizer: Mapping[str, Any] | None = None  # an inline Organization or an `@id` reference


def event_id(url: str) -> str:
    return f"{url}#event"


def _clean(values: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value not in ("", None, [], ())}


def _place(facts: EventFacts) -> dict[str, Any]:
    address = _clean(
        {
            "@type": "PostalAddress",
            "streetAddress": facts.street,
            "postalCode": facts.postcode,
            "addressLocality": facts.city,
            "addressRegion": facts.region,
            "addressCountry": facts.country,
        }
    )
    has_address = len(address) > 1
    geo = (
        {
            "@type": "GeoCoordinates",
            "latitude": facts.latitude,
            "longitude": facts.longitude,
        }
        if facts.latitude is not None and facts.longitude is not None
        else ""
    )
    name = facts.venue_name or facts.city
    if not (name or has_address or geo):
        return {}
    return _clean(
        {"@type": "Place", "name": name, "address": address if has_address else "", "geo": geo}
    )


def _location(facts: EventFacts) -> Any:
    places: list[dict[str, Any]] = []
    if facts.attendance in {"in_person", "mixed"}:
        place = _place(facts)
        if place:
            places.append(place)
    if facts.attendance in {"online", "mixed"} and facts.online_url:
        places.append({"@type": "VirtualLocation", "url": facts.online_url})
    if not places:
        return ""
    return places[0] if len(places) == 1 else places


def event_node(facts: EventFacts) -> dict[str, Any]:
    """The `Event`, or {} when it has no place and no online link (not a rich-result event)."""
    location = _location(facts)
    if not location or facts.attendance not in ATTENDANCE_MODES:
        return {}
    status = "EventCancelled" if facts.cancelled else "EventScheduled"
    return _clean(
        {
            "@type": "Event",
            "@id": event_id(facts.url),
            "url": facts.url,
            "name": facts.name,
            "description": facts.description,
            "startDate": facts.start.isoformat(),
            "endDate": facts.end.isoformat(),
            "eventStatus": f"{SCHEMA_URL}/{status}",
            "eventAttendanceMode": f"{SCHEMA_URL}/{ATTENDANCE_MODES[facts.attendance]}",
            "location": location,
            "image": facts.image,
            "organizer": dict(facts.organizer) if facts.organizer else "",
            "inLanguage": facts.language,
        }
    )


def occurrence_bounds(occurrences: Sequence[Any], fallback: tuple[Any, Any]) -> tuple[Any, Any]:
    """The next occurrence's (start, end), else `fallback` (the first and last known)."""
    if occurrences:
        return occurrences[0].start, occurrences[0].end
    return fallback
