"""EVT.10: the pure `Event` node builder (no database)."""

import datetime as dt
import zoneinfo
from dataclasses import replace
from decimal import Decimal

from apps.seo import event_graph as eg

ROME = zoneinfo.ZoneInfo("Europe/Rome")
URL = "https://shop.example/events/fair/"
BASE = eg.EventFacts(
    url=URL,
    name="Fair",
    description="A fair.",
    start=dt.datetime(2027, 3, 10, 9, 0, tzinfo=ROME),
    end=dt.datetime(2027, 3, 10, 17, 0, tzinfo=ROME),
    language="it",
    attendance="in_person",
    venue_name="Hall",
    street="Via Esempio 1",
    postcode="20121",
    city="Milano",
    region="MI",
    country="IT",
    latitude=45.464204,
    longitude=9.189982,
)


def test_offline_with_address_and_geo() -> None:
    node = eg.event_node(BASE)
    assert node["@id"] == f"{URL}#event"
    assert node["startDate"] == "2027-03-10T09:00:00+01:00"
    assert node["eventStatus"] == "https://schema.org/EventScheduled"
    assert node["eventAttendanceMode"] == "https://schema.org/OfflineEventAttendanceMode"
    assert node["location"] == {
        "@type": "Place",
        "name": "Hall",
        "address": {
            "@type": "PostalAddress",
            "streetAddress": "Via Esempio 1",
            "postalCode": "20121",
            "addressLocality": "Milano",
            "addressRegion": "MI",
            "addressCountry": "IT",
        },
        "geo": {"@type": "GeoCoordinates", "latitude": 45.464204, "longitude": 9.189982},
    }
    assert node["inLanguage"] == "it"
    assert "organizer" not in node
    assert "image" not in node


def test_geo_needs_both_coordinates() -> None:
    node = eg.event_node(replace(BASE, longitude=None))
    assert "geo" not in node["location"]


def test_online() -> None:
    facts = replace(BASE, attendance="online", online_url="https://meet.example/x")
    node = eg.event_node(facts)
    assert node["eventAttendanceMode"] == "https://schema.org/OnlineEventAttendanceMode"
    assert node["location"] == {"@type": "VirtualLocation", "url": "https://meet.example/x"}


def test_mixed_has_both_locations() -> None:
    facts = replace(BASE, attendance="mixed", online_url="https://meet.example/x")
    node = eg.event_node(facts)
    assert node["eventAttendanceMode"] == "https://schema.org/MixedEventAttendanceMode"
    assert [place["@type"] for place in node["location"]] == ["Place", "VirtualLocation"]


def test_cancelled() -> None:
    node = eg.event_node(replace(BASE, cancelled=True))
    assert node["eventStatus"] == "https://schema.org/EventCancelled"


def test_all_day_uses_dates() -> None:
    facts = replace(BASE, start=dt.date(2027, 8, 28), end=dt.date(2027, 9, 2))
    node = eg.event_node(facts)
    assert (node["startDate"], node["endDate"]) == ("2027-08-28", "2027-09-02")


def test_summer_offset() -> None:
    facts = replace(BASE, start=dt.datetime(2027, 7, 10, 9, 0, tzinfo=ROME))
    assert eg.event_node(facts)["startDate"] == "2027-07-10T09:00:00+02:00"


def test_organizer_vendor_operator_and_none() -> None:
    vendor = {"@type": "Organization", "name": "Vendor A"}
    assert eg.event_node(replace(BASE, organizer=vendor))["organizer"] == vendor
    operator = {"@id": "https://shop.example/#organization"}
    assert eg.event_node(replace(BASE, organizer=operator))["organizer"] == operator
    assert "organizer" not in eg.event_node(replace(BASE, organizer=None))


def test_no_place_and_no_link_gives_no_node() -> None:
    bare = replace(
        BASE, venue_name="", street="", postcode="", city="", region="", country="",
        latitude=None, longitude=None,
    )  # fmt: skip
    assert eg.event_node(bare) == {}
    assert eg.event_node(replace(BASE, attendance="online", online_url="")) == {}
    assert eg.event_node(replace(BASE, attendance="")) == {}


def test_occurrence_bounds() -> None:
    class Occ:
        start, end = 1, 2

    assert eg.occurrence_bounds([Occ()], (8, 9)) == (1, 2)
    assert eg.occurrence_bounds([], (8, 9)) == (8, 9)


PREVIOUS = dt.datetime(2027, 2, 10, 9, 0, tzinfo=ROME)


def test_scheduled_has_no_offers_and_no_previous_start() -> None:
    node = eg.event_node(BASE)
    assert node["eventStatus"] == "https://schema.org/EventScheduled"
    assert "offers" not in node
    assert "previousStartDate" not in node


def test_postponed_with_previous_start() -> None:
    node = eg.event_node(replace(BASE, postponed=True, previous_start=PREVIOUS))
    assert node["eventStatus"] == "https://schema.org/EventPostponed"
    assert node["previousStartDate"] == "2027-02-10T09:00:00+01:00"


def test_postponed_without_previous_start() -> None:
    node = eg.event_node(replace(BASE, postponed=True))
    assert node["eventStatus"] == "https://schema.org/EventPostponed"
    assert "previousStartDate" not in node


def test_cancelled_wins_over_postponed() -> None:
    node = eg.event_node(replace(BASE, cancelled=True, postponed=True, previous_start=PREVIOUS))
    assert node["eventStatus"] == "https://schema.org/EventCancelled"
    assert "previousStartDate" not in node


def test_previous_start_is_ignored_when_not_postponed() -> None:
    assert "previousStartDate" not in eg.event_node(replace(BASE, previous_start=PREVIOUS))


def test_priced_offer_is_a_two_place_string() -> None:
    node = eg.event_node(
        replace(BASE, price=Decimal("12.5"), ticket_url="https://tickets.example/fair")
    )
    assert node["offers"] == {
        "@type": "Offer",
        "url": "https://tickets.example/fair",
        "price": "12.50",
        "priceCurrency": "EUR",
    }
    assert "availability" not in node["offers"]


def test_free_offer_has_price_zero() -> None:
    node = eg.event_node(replace(BASE, is_free=True))
    assert node["offers"] == {"@type": "Offer", "price": "0", "priceCurrency": "EUR"}


def test_link_only_offer_has_no_price() -> None:
    node = eg.event_node(replace(BASE, ticket_url="https://tickets.example/fair"))
    assert node["offers"] == {"@type": "Offer", "url": "https://tickets.example/fair"}


def test_free_event_is_accessible_for_free_and_a_priced_one_is_not_marked() -> None:
    assert eg.event_node(replace(BASE, is_free=True))["isAccessibleForFree"] is True
    assert "isAccessibleForFree" not in eg.event_node(replace(BASE, price=Decimal("5")))


def test_offer_currency_is_the_platform_currency() -> None:
    from apps.core.money import CURRENCY

    node = eg.event_node(replace(BASE, price=Decimal("5")))
    assert node["offers"]["priceCurrency"] == CURRENCY
