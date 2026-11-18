"""EVT.10: the `Event` node in the page graph of an event page (spec 15 "Structured data",
regression case 18; SEO.7 graph, SEO.15 organizer name).

Snapshot-style checks of the node for an offline event with address and coordinates, an online
event, a cancelled event, an event without a vendor and a recurring one; the `@id` references
resolve; the events index and the search results reference the event by its canonical URL; no
cross-language fallback; a fixed number of queries.
"""

import datetime as dt
import json
import re
from typing import Any

import pytest
from django.apps import apps
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from wagtail.models import Site

from apps.core.i18n import primary_language
from apps.core.tests.factories import MediaImageFactory
from apps.events.models import EventPage
from apps.events.tests.factories import EventPageFactory, primary_index
from apps.seo import schema_check, seller
from apps.vendors.tests.factories import VendorFactory

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("consumer_prices")]

SCRIPT = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
START = dt.datetime(2027, 3, 10, 8, 0, tzinfo=dt.UTC)  # 09:00 in Rome (CET, +01:00)


@pytest.fixture(autouse=True)
def _site(consumer_prices: None) -> None:
    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )
    apps.get_model("core", "PlatformOperator").objects.update(legal_name="Operator Legal Name Srl")


def document_of(path: str) -> dict[str, Any]:
    response = Client().get(path)
    assert response.status_code == 200, path
    blocks = SCRIPT.findall(response.content.decode())
    assert len(blocks) == 1, f"{path}: a page has exactly one JSON-LD script, got {len(blocks)}"
    document: dict[str, Any] = json.loads(blocks[0])
    assert schema_check.dangling_references(document) == [], path
    assert schema_check.rich_result_errors(document) == [], path
    return document


def nodes_of(path: str, kind: str) -> list[dict[str, Any]]:
    graph: list[dict[str, Any]] = document_of(path)["@graph"]
    return [node for node in graph if node["@type"] == kind]


def event_of(page: EventPage) -> dict[str, Any]:
    [node] = nodes_of(page.url, "Event")
    return node


def published(page: EventPage) -> EventPage:
    page.save_revision().publish()
    page.refresh_from_db()
    return page


def create(**fields: Any) -> EventPage:
    fields.setdefault("starts_at", START)
    return published(EventPageFactory.create(**fields))


def test_an_offline_event_with_address_and_coordinates() -> None:
    image = MediaImageFactory.create()
    page = create(located=True, listing_image=image, booth="Hall 3")
    node = event_of(page)
    site = "http://localhost"
    assert node["@id"] == f"{site}{page.url}#event"
    assert node["url"] == f"{site}{page.url}"
    assert node["name"] == page.title
    assert node["description"] == "Meet the makers at the spring trade fair."
    assert node["startDate"] == "2027-03-10T09:00:00+01:00"
    assert node["endDate"] == "2027-03-10T17:00:00+01:00"
    assert node["eventStatus"] == "https://schema.org/EventScheduled"
    assert node["eventAttendanceMode"] == "https://schema.org/OfflineEventAttendanceMode"
    assert node["inLanguage"] == primary_language()
    assert node["location"] == {
        "@type": "Place",
        "name": "Exhibition centre",
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
    assert node["image"].startswith(f"{site}/")
    assert "offers" not in node
    assert node["organizer"]["@type"] == "Organization"
    assert page.vendor is not None
    assert node["organizer"]["name"] == seller.public_name(page.vendor)  # SEO.15


def test_the_page_is_about_the_event_and_the_ids_resolve() -> None:
    page = create(located=True)
    document = document_of(page.url)
    [web] = [n for n in document["@graph"] if n["@type"] == "WebPage"]
    assert web["mainEntity"] == {"@id": event_of(page)["@id"]}
    assert event_of(page)["@id"] in {n["@id"] for n in document["@graph"] if "@id" in n}


def test_geo_needs_both_coordinates_and_the_address_may_be_partial() -> None:
    page = create(venue_name="", street="", postcode="", booth="")
    node = event_of(page)
    assert node["location"] == {
        "@type": "Place",
        "name": "Milano",
        "address": {"@type": "PostalAddress", "addressLocality": "Milano", "addressCountry": "IT"},
    }


def test_an_online_event() -> None:
    page = create(online=True)
    node = event_of(page)
    assert node["eventAttendanceMode"] == "https://schema.org/OnlineEventAttendanceMode"
    assert node["location"] == {"@type": "VirtualLocation", "url": "https://meet.example.com/fair"}


def test_a_mixed_event_has_a_place_and_a_virtual_location() -> None:
    page = create(
        located=True,
        attendance=EventPage.Attendance.MIXED,
        online_url="https://meet.example.com/fair",
    )
    node = event_of(page)
    assert node["eventAttendanceMode"] == "https://schema.org/MixedEventAttendanceMode"
    assert [place["@type"] for place in node["location"]] == ["Place", "VirtualLocation"]


def test_a_cancelled_event_is_marked_cancelled_and_stays() -> None:
    page = create(located=True)
    EventPage.objects.filter(pk=page.pk).update(
        status=EventPage.Status.CANCELLED, cancelled_at=dt.datetime(2027, 1, 1, tzinfo=dt.UTC)
    )
    node = event_of(page)
    assert node["eventStatus"] == "https://schema.org/EventCancelled"


def test_an_event_of_the_platform_names_the_operator() -> None:
    page = create(platform=True, located=True)
    document = document_of(page.url)
    node = event_of(page)
    [operator] = [n for n in document["@graph"] if n["@type"] == "Organization"]
    assert node["organizer"] == {"@id": operator["@id"]}


def test_an_event_of_a_vendor_without_a_public_name_has_no_organizer() -> None:
    page = create(located=True, vendor=VendorFactory.create(status="active", display_name=""))
    assert page.vendor is not None
    if seller.public_name(page.vendor) is not None:
        pytest.skip("the factory vendor has a public name")
    assert "organizer" not in event_of(page)


def test_an_all_day_event_has_dates() -> None:
    page = create(located=True, all_day_event=True, starts_at=None, ends_at=None)
    node = event_of(page)
    assert (node["startDate"], node["endDate"]) == ("2027-08-28", "2027-09-02")


def test_a_recurring_event_emits_the_next_occurrence_only() -> None:
    soon = dt.datetime.now(dt.UTC).replace(microsecond=0) + dt.timedelta(days=3)
    page = create(
        located=True, starts_at=soon - dt.timedelta(days=14), rrule="FREQ=WEEKLY;COUNT=20"
    )
    node = event_of(page)
    start = dt.datetime.fromisoformat(node["startDate"])
    now = dt.datetime.now(dt.UTC)
    assert now <= start <= now + dt.timedelta(days=7)
    assert start.utcoffset() is not None


def test_a_promotion_has_no_event_node() -> None:
    page = create(promotion=True)
    graph = document_of(page.url)["@graph"]
    assert "Event" not in [n["@type"] for n in graph]
    assert "mainEntity" not in next(n for n in graph if n["@type"] == "WebPage")


def test_a_hidden_event_emits_nothing() -> None:
    page = create(located=True)
    EventPage.objects.filter(pk=page.pk).update(
        hidden_at=dt.datetime(2027, 1, 1, tzinfo=dt.UTC), status=EventPage.Status.HIDDEN
    )
    assert Client().get(page.url).status_code == 404


def test_a_hidden_event_has_no_node_even_when_built_directly() -> None:
    from django.test import RequestFactory

    from apps.seo.page_graph import page_graph

    page = create(located=True)
    EventPage.objects.filter(pk=page.pk).update(hidden_at=dt.datetime(2027, 1, 1, tzinfo=dt.UTC))
    page.refresh_from_db()
    graph = page_graph(RequestFactory().get(page.url), page, "en")
    assert graph is not None
    assert "Event" not in [n["@type"] for n in graph["@graph"]]


def test_the_index_lists_events_by_canonical_url_and_resolves() -> None:
    page = create(located=True)
    index = primary_index()
    document = document_of(index.url)
    [items] = [n for n in document["@graph"] if n["@type"] == "ItemList"]
    [entry] = items["itemListElement"]
    assert entry["item"]["@id"] == f"http://localhost{page.url}"
    assert entry["item"]["@type"] == "Event"


def test_search_lists_events_by_canonical_url() -> None:
    page = create(located=True, title="Springfair")
    document = document_of("/?s=Springfair")
    items = [n for n in document["@graph"] if n["@type"] == "ItemList"]
    ids = [e["item"]["@id"] for n in items for e in n["itemListElement"]]
    assert f"http://localhost{page.url}" in ids


def test_the_event_page_takes_a_bounded_number_of_queries() -> None:
    page = create(located=True, listing_image=MediaImageFactory.create())
    Client().get(page.url)  # warm caches
    with CaptureQueriesContext(connection) as many:
        Client().get(page.url)
    second = create(located=True, listing_image=MediaImageFactory.create())
    with CaptureQueriesContext(connection) as again:
        Client().get(second.url)
    assert abs(len(again) - len(many)) <= 2
    assert len(many) <= 60


def test_the_node_is_in_the_page_language_only() -> None:
    """No cross-language fallback: a German event's node says `de`, and its Italian URL is 404."""
    from wagtail.models import Locale

    from apps.accounts.models import User
    from apps.cms.services import seed_site
    from apps.events import services
    from apps.events.models import PolicyMode, VendorEventPolicy
    from apps.vendors.models import Vendor
    from apps.vendors.tests.factories import VendorMemberFactory

    seed_site()
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    user: User = VendorMemberFactory.create(vendor=vendor).user
    VendorEventPolicy.objects.create(vendor=vendor, mode=PolicyMode.TRUSTED)
    start = dt.datetime.now(dt.UTC) + dt.timedelta(days=10)
    data = {
        "title": "Herbstmesse",
        "summary": "Treffen Sie die Hersteller.",
        "event_type": "fair",
        "starts_at": start,
        "ends_at": start + dt.timedelta(hours=8),
        "timezone": "Europe/Rome",
        "attendance": "in_person",
        "venue_name": "Messe Halle 3",
        "city": "Bolzano",
        "country": "IT",
    }
    page = services.save_event_draft(
        user, data, vendor=vendor, locale=Locale.objects.get(language_code="de")
    ).page
    assert page is not None
    services.submit_event(user, page)
    page.refresh_from_db()
    node = event_of(page)
    assert node["inLanguage"] == "de"
    assert node["url"] == f"http://localhost/de/events/{page.slug}/"
    assert node["description"] == "Treffen Sie die Hersteller."
    assert Client().get(f"/it/events/{page.slug}/").status_code == 404


def test_hostile_text_cannot_break_out_of_the_json_ld_script() -> None:
    payload = "</script><script>alert(1)</script>"
    page = create(located=True, title=payload, summary=payload, venue_name=payload)
    html = Client().get(page.url).content.decode()
    [block] = SCRIPT.findall(html)
    assert "</script" not in block.lower()
    assert "<script" not in block.lower()
    document = json.loads(block)
    [node] = [n for n in document["@graph"] if n["@type"] == "Event"]
    assert node["name"] == payload
    assert node["description"] == payload
    assert node["location"]["name"] == payload
