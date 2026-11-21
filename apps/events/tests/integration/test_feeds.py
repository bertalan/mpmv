"""EVT.9: RSS 2.0 and ICS feeds per language (spec 15 "Feeds and calendars"; regression cases
10 (feed part), 12, 13, 14, 15, 16 and 20). Every feed is parsed: ICS with `icalendar`, RSS with a
small RSS 2.0 structure check. Dates are relative to now so the tests do not age."""

import datetime as dt
import re
from email.utils import parsedate_to_datetime
from typing import Any

import pytest
from defusedxml.ElementTree import fromstring
from django.apps import apps
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from icalendar import Calendar
from wagtail.models import Site

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.cms.services import seed_site
from apps.core.models import Throttle
from apps.events.models import EventIndexPage, EventPage
from apps.events.services import hide_event
from apps.events.tests.factories import EventPageFactory, primary_index
from apps.events.tests.integration.test_multilingual_events import Fixture
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory
from tests.i18n import other_language, primary_language, translated

pytestmark = pytest.mark.django_db

DC = "{http://purl.org/dc/elements/1.1/}"
GEORSS = "{http://www.georss.org/georss}"
ATOM = "{http://www.w3.org/2005/Atom}"


@pytest.fixture(autouse=True)
def _site() -> None:
    seed_site()
    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )


def _index_path() -> str:
    return str(primary_index().url)


def _index_in(language: str) -> str:
    """The path of the events index in `language` (no prefix for the primary one)."""
    return str(EventIndexPage.objects.get(locale__language_code=language).url)


def _event(days: int = 10, **fields: Any) -> EventPage:
    start = (timezone.now() + dt.timedelta(days=days)).replace(minute=0, second=0, microsecond=0)
    publish = fields.pop("publish", True)
    page = EventPageFactory.create(starts_at=start, ends_at=start + dt.timedelta(hours=8), **fields)
    if publish:
        page.save_revision().publish()
        page.refresh_from_db()
    return page


def _rss(client: Client, path: str) -> Any:
    response = client.get(path)
    assert response.status_code == 200, (path, response.status_code)
    assert response["Content-Type"].startswith("application/rss+xml")
    return assert_valid_rss(response.content)


def _ics(client: Client, path: str) -> Calendar:
    response = client.get(path)
    assert response.status_code == 200, (path, response.status_code)
    assert response["Content-Type"] == "text/calendar; charset=utf-8"
    return Calendar.from_ical(response.content)


def assert_valid_rss(content: bytes) -> Any:
    """The RSS 2.0 rules this feed relies on: one channel with title, link and description, items
    with a title or description, a non-permalink guid and an RFC 822 date."""
    root = fromstring(content)
    assert root.tag == "rss"
    assert root.get("version") == "2.0"
    channel = root.find("channel")
    assert channel is not None
    for required in ("title", "link", "description"):
        assert (channel.findtext(required) or "").strip(), required
    for item in channel.findall("item"):
        assert item.findtext("title") or item.findtext("description")
        guid = item.find("guid")
        assert guid is not None
        assert guid.get("isPermaLink") == "false"
        assert (guid.text or "").startswith("urn:uuid:")
        assert parsedate_to_datetime(item.findtext("pubDate") or "")
    return channel


def _titles(channel: Any) -> list[str]:
    return [item.findtext("title") or "" for item in channel.findall("item")]


def _summaries(calendar: Calendar) -> list[str]:
    return [str(event["SUMMARY"]) for event in calendar.walk("VEVENT")]


# --- Platform, vendor and type feeds


def test_the_platform_rss_lists_each_event_once_with_a_stable_guid() -> None:
    client = Client()
    page = _event(10, title="Spring fair", located=True)
    channel = _rss(client, _index_path() + "feed.rss")
    item = channel.find("item")
    assert _titles(channel) == ["Spring fair"]
    assert item.findtext("guid") == f"urn:uuid:{page.translation_key}"
    assert item.findtext("link", "").endswith(str(page.url))
    assert item.findtext("category") == translated("Trade fair or meeting")
    assert channel.findtext("language") == page.locale.language_code
    assert channel.find(f"{ATOM}link").get("rel") == "self"
    assert item.findtext(f"{DC}language") is None  # no fallback: no language override


def test_the_platform_ics_parses_and_has_the_calendar_properties() -> None:
    client = Client()
    page = _event(10, title="Spring fair")
    calendar = _ics(client, _index_path() + "calendar.ics")
    assert _summaries(calendar) == ["Spring fair"]
    assert str(calendar.walk("VEVENT")[0]["UID"]).startswith(f"{page.translation_key}@")
    assert str(calendar["METHOD"]) == "PUBLISH"
    assert len(calendar.walk("VTIMEZONE")) == 1


def test_vendor_and_type_feeds_filter_by_scope() -> None:
    client = Client()
    mine = _event(10, title="Mine")
    _event(11, title="Theirs", promotion=True)
    base = _index_path()
    vendor = mine.vendor
    assert vendor is not None
    assert _titles(_rss(client, f"{base}vendor/{vendor.slug}/feed.rss")) == ["Mine"]
    assert _summaries(_ics(client, f"{base}vendor/{vendor.slug}/calendar.ics")) == ["Mine"]
    assert _titles(_rss(client, f"{base}type/promotion/feed.rss")) == ["Theirs"]
    assert _summaries(_ics(client, f"{base}type/fair/calendar.ics")) == ["Mine"]


def test_an_unknown_vendor_or_type_is_a_404() -> None:
    client = Client()
    base = _index_path()
    assert client.get(f"{base}vendor/no-such-vendor/feed.rss").status_code == 404
    assert client.get(f"{base}type/no-such-type/calendar.ics").status_code == 404


def test_a_feed_holds_at_most_500_items() -> None:
    from apps.events import feeds

    assert feeds.MAX_ITEMS == 500


# --- Case 12: hidden, draft and pending events are in no feed


def _all_titles(client: Client, vendor: Any, kind: str = "fair") -> dict[str, list[str]]:
    base = _index_path()
    scopes = ("", f"vendor/{vendor.slug}/", f"type/{kind}/")
    found: dict[str, list[str]] = {}
    for scope in scopes:
        found["rss " + scope] = _titles(_rss(client, f"{base}{scope}feed.rss"))
        found["ics " + scope] = _summaries(_ics(client, f"{base}{scope}calendar.ics"))
    return found


def test_a_hidden_event_leaves_every_feed_and_its_single_ics_is_a_404() -> None:
    client = Client()
    page = _event(10, title="Soon hidden")
    assert all(titles == ["Soon hidden"] for titles in _all_titles(client, page.vendor).values())
    EventPage.objects.filter(pk=page.pk).update(hidden_at=timezone.now())
    assert all(titles == [] for titles in _all_titles(client, page.vendor).values())
    assert client.get(f"{page.url}calendar.ics").status_code == 404
    assert client.get(str(page.url)).status_code == 404


def test_draft_and_pending_events_are_in_no_feed() -> None:
    client = Client()
    live = _event(10, title="Live")
    _event(11, title="Draft", publish=False, vendor=live.vendor)
    pending = _event(12, title="Pending", publish=False, vendor=live.vendor)
    pending.save_revision()
    assert all(titles == ["Live"] for titles in _all_titles(client, live.vendor).values())


def test_an_event_of_a_suspended_vendor_is_in_no_feed() -> None:
    client = Client()
    vendor = VendorFactory.create(status=Vendor.Status.SUSPENDED)
    _event(10, title="Suspended", vendor=vendor)
    assert _titles(_rss(client, _index_path() + "feed.rss")) == []


# --- Case 13: a cancelled event stays for the retention window


def test_a_cancelled_event_stays_with_status_cancelled_then_disappears() -> None:
    client = Client()
    page = _event(10, title="Called off", sequence=3, cancelled_at=timezone.now())
    calendar = _ics(client, _index_path() + "calendar.ics")
    event = calendar.walk("VEVENT")[0]
    assert str(event["STATUS"]) == "CANCELLED"
    assert event["SEQUENCE"] == 3
    assert str(event["UID"]).startswith(f"{page.translation_key}@")
    item = _rss(client, _index_path() + "feed.rss").find("item")
    assert item.findtext("title").endswith("Called off")
    assert item.findtext("title") != "Called off"
    assert item.findtext("guid") == f"urn:uuid:{page.translation_key}"
    EventPage.objects.filter(pk=page.pk).update(cancelled_at=timezone.now() - dt.timedelta(days=31))
    assert _summaries(_ics(client, _index_path() + "calendar.ics")) == []
    assert _titles(_rss(client, _index_path() + "feed.rss")) == []


def test_an_event_that_ended_over_30_days_ago_is_not_in_the_feeds() -> None:
    client = Client()
    _event(-40, title="Long gone")
    assert _titles(_rss(client, _index_path() + "feed.rss")) == []


# --- Case 14: a recurring event is one VEVENT


def test_a_recurring_event_is_one_vevent_with_rrule_and_exdate() -> None:
    client = Client()
    start = (timezone.now() + dt.timedelta(days=10)).replace(minute=0, second=0, microsecond=0)
    _event(
        10,
        title="Weekly market",
        rrule="FREQ=WEEKLY;COUNT=8",
        exdates=[start + dt.timedelta(days=14)],
    )
    response = client.get(_index_path() + "calendar.ics")
    body = response.content.decode()
    events = Calendar.from_ical(response.content).walk("VEVENT")
    assert len(events) == 1
    assert "RRULE:FREQ=WEEKLY;COUNT=8\r\n" in body
    assert body.count("EXDATE") == 1
    assert len(_rss(client, _index_path() + "feed.rss").findall("item")) == 1


# --- Case 15: ETag, Last-Modified, 304, Cache-Control


@pytest.mark.parametrize("name", ["feed.rss", "calendar.ics"])
def test_a_repeated_request_with_the_etag_gets_304_and_a_hide_changes_the_etag(name: str) -> None:
    client = Client()
    page = _event(10, title="Etag fair")
    path = _index_path() + name
    first = client.get(path)
    etag = first["ETag"]
    assert etag.startswith('"')
    assert not etag.startswith("W/")
    assert first["Cache-Control"] == "public, max-age=300, s-maxage=60"
    assert first["Last-Modified"]
    again = client.get(path, headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert again.content == b""
    # Last-Modified is informational: only the strong ETag decides a 304
    later = client.get(path, headers={"If-Modified-Since": first["Last-Modified"]})
    assert later.status_code == 200
    EventPage.objects.filter(pk=page.pk).update(hidden_at=timezone.now())
    changed = client.get(path, headers={"If-None-Match": etag})
    assert changed.status_code == 200
    assert changed["ETag"] != etag


def test_last_modified_is_the_latest_content_change_in_scope() -> None:
    client = Client()
    changed = timezone.now().replace(microsecond=0) - dt.timedelta(days=2)
    page = _event(10)
    EventPage.objects.filter(pk=page.pk).update(content_changed_at=changed)
    response = client.get(_index_path() + "feed.rss")
    assert parsedate_to_datetime(response["Last-Modified"]) == changed


def test_the_body_is_the_same_between_changes() -> None:
    client = Client()
    _event(10)
    path = _index_path() + "calendar.ics"
    assert client.get(path).content == client.get(path).content


# --- Case 16: no personal data


def test_no_feed_contains_an_email_a_phone_or_a_member_name() -> None:
    client = Client()
    page = _event(10, title="Private fair", located=True)
    vendor = page.vendor
    assert vendor is not None
    member = User.objects.create(email="secret.member@example.test", full_name="Zoltan Quxbar")
    page.owner = member
    page.save()
    page.save_revision().publish()
    base = _index_path()
    for path in (
        f"{base}feed.rss",
        f"{base}calendar.ics",
        f"{base}vendor/{vendor.slug}/feed.rss",
        f"{base}vendor/{vendor.slug}/calendar.ics",
        f"{page.url}calendar.ics",
    ):
        text = client.get(path).content.decode()
        assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", re.sub(r"UID:[^\r\n]*", "", text)), path
        assert not re.search(
            r"\+?\d[\d ()-]{8,}\d",
            re.sub(
                r"\d{8}T\d{6}Z?|urn:uuid:\S+|[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", "", text
            ),
        )
        assert "Zoltan" not in text
        assert "Quxbar" not in text


# --- Case 20: GEO and georss:point only with coordinates


def test_geo_and_georss_point_only_when_the_event_has_coordinates() -> None:
    client = Client()
    _event(10, title="Located", located=True)
    _event(11, title="Unlocated", venue_name="Hall 2")
    base = _index_path()
    ics = client.get(base + "calendar.ics").content.decode()
    assert ics.count("GEO:45.464204;9.189982\r\n") == 1
    assert ics.count("GEO:") == 1
    channel = _rss(client, base + "feed.rss")
    points = {
        item.findtext("title"): item.findtext(f"{GEORSS}point") for item in channel.findall("item")
    }
    assert points == {"Located": "45.464204 9.189982", "Unlocated": None}


# --- Case 10 (feed part): the original-language fallback


def test_the_feeds_of_one_language_carry_an_event_written_in_another_with_its_language() -> None:
    client = Client()
    fixture = Fixture()
    original, view = other_language(), primary_language()
    page = fixture.create(original, title="Herbstmesse")
    feed, calendar = _index_in(view) + "feed.rss", _index_in(view) + "calendar.ics"
    channel = _rss(client, feed)
    item = channel.find("item")
    assert channel.findtext("language") == view
    assert item.findtext(f"{DC}language") == original
    assert item.findtext("link", "").endswith(str(page.url))
    response = client.get(calendar)
    event = Calendar.from_ical(response.content).walk("VEVENT")[0]
    assert event["SUMMARY"].params["LANGUAGE"] == original
    assert str(event["URL"]).endswith(str(page.url))
    uid = str(event["UID"])
    translation = fixture.translate(page, view, title="Fiera d'autunno")
    item = _rss(client, feed).find("item")
    assert item.findtext("title") == "Fiera d'autunno"
    assert item.findtext(f"{DC}language") is None
    event = Calendar.from_ical(client.get(calendar).content).walk("VEVENT")[0]
    assert "LANGUAGE" not in event["SUMMARY"].params
    assert str(event["UID"]) == uid  # one UID in every language
    assert str(event["URL"]).endswith(str(translation.url))
    in_original = client.get(_index_in(original) + "calendar.ics").content
    assert str(Calendar.from_ical(in_original).walk("VEVENT")[0]["UID"]) == uid


# --- Rate limit


def test_the_31st_request_in_a_minute_from_one_ip_gets_429_with_retry_after() -> None:
    client = Client()
    path = _index_path() + "feed.rss"
    for _ in range(30):
        assert client.get(path, REMOTE_ADDR="203.0.113.7").status_code == 200
    blocked = client.get(path, REMOTE_ADDR="203.0.113.7")
    assert blocked.status_code == 429
    assert 1 <= int(blocked["Retry-After"]) <= 60
    assert "public" not in blocked.get("Cache-Control", "")
    assert client.get(path, REMOTE_ADDR="203.0.113.8").status_code == 200
    assert client.get(_index_path() + "calendar.ics", REMOTE_ADDR="203.0.113.7").status_code == 200


def test_a_304_answer_counts_towards_the_limit() -> None:
    client = Client()
    _event(10)
    path = _index_path() + "feed.rss"
    etag = client.get(path, REMOTE_ADDR="203.0.113.9")["ETag"]
    for _ in range(29):
        assert (
            client.get(path, headers={"If-None-Match": etag}, REMOTE_ADDR="203.0.113.9").status_code
            == 304
        )
    assert client.get(path, REMOTE_ADDR="203.0.113.9").status_code == 429


# --- Rate limit: validated first, bounded key, no address, spoofed header


def test_a_404_scope_writes_no_throttle_row_and_a_400_char_path_is_not_a_500() -> None:
    client = Client()
    base = _index_path()
    long_slug = "x" * 400
    for path in (
        f"{base}vendor/{long_slug}/feed.rss",
        f"{base}vendor/{long_slug}/calendar.ics",
        f"{base}type/{long_slug}/feed.rss",
        f"{base}vendor/nobody/feed.rss",
    ):
        assert client.get(path, REMOTE_ADDR="203.0.113.20").status_code == 404, path
    assert not Throttle.objects.filter(key__startswith="events.feed:").exists()


def test_a_valid_feed_key_is_bounded_whatever_the_path() -> None:
    client = Client()
    page = _event(10)
    assert client.get(_index_path() + "feed.rss", REMOTE_ADDR="2001:db8::1").status_code == 200
    assert client.get(f"{page.url}calendar.ics", REMOTE_ADDR="2001:db8::1").status_code == 200
    rows = Throttle.objects.filter(key__startswith="events.feed:")
    keys = list(rows.values_list("key", flat=True))
    assert len(keys) == 2
    assert all(len(key) <= 320 for key in keys)


def test_a_request_without_a_client_address_is_not_throttled(caplog: Any) -> None:
    client = Client()
    path = _index_path() + "feed.rss"
    with caplog.at_level("WARNING", logger="apps.events.feeds"):
        for _ in range(35):
            assert client.get(path, REMOTE_ADDR="").status_code == 200
    assert not Throttle.objects.filter(key__startswith="events.feed:").exists()
    assert "without a client address" in caplog.text


def test_a_spoofed_forwarded_header_does_not_change_the_bucket(settings: Any) -> None:
    settings.TRUSTED_PROXY_HEADER = "X-Forwarded-For"
    settings.TRUSTED_PROXIES = ["198.51.100.1"]
    client = Client()
    path = _index_path() + "feed.rss"
    for number in range(30):  # REMOTE_ADDR is not a trusted proxy: the header is ignored
        response = client.get(
            path, REMOTE_ADDR="203.0.113.30", headers={"X-Forwarded-For": f"192.0.2.{number}"}
        )
        assert response.status_code == 200
    blocked = client.get(
        path, REMOTE_ADDR="203.0.113.30", headers={"X-Forwarded-For": "192.0.2.200"}
    )
    assert blocked.status_code == 429


# --- Query counts: flat in the number of events


def _queries(client: Client, path: str) -> int:
    with CaptureQueriesContext(connection) as captured:
        assert client.get(path, REMOTE_ADDR="203.0.113.40").status_code == 200
    return len(captured)


@pytest.mark.parametrize("name", ["feed.rss", "calendar.ics"])
def test_the_query_count_does_not_grow_with_the_number_of_events(name: str) -> None:
    client = Client()
    vendor = VendorFactory.create()
    for number in range(3):
        _event(10 + number, vendor=vendor, located=True)
    path = _index_path() + name
    client.get(path, REMOTE_ADDR="203.0.113.40")  # warm caches and the throttle row
    few = _queries(client, path)
    for number in range(27):
        _event(20 + number, vendor=VendorFactory.create(), located=True)
    assert _queries(client, path) == few


# --- Hostile content, hide service, suspended vendor, LOCATION language


HOSTILE = "Fair <b> & ]]> , ; \\ line\r\nEND:VEVENT caf\u00e9 \u00e8 \u4e2d"


def test_hostile_text_stays_well_formed_in_rss_and_cannot_inject_ics_lines() -> None:
    client = Client()
    _event(
        10,
        title=HOSTILE,
        summary=HOSTILE,
        venue_name=HOSTILE,
        located=True,
    )
    channel = _rss(client, _index_path() + "feed.rss")
    item = channel.find("item")
    assert "&" in item.findtext("title")
    assert "]]>" in item.findtext("title")
    assert "caf\u00e9" in item.findtext("description")
    response = client.get(_index_path() + "calendar.ics")
    calendar = Calendar.from_ical(response.content)
    assert len(calendar.walk("VEVENT")) == 1
    assert "caf\u00e9" in str(calendar.walk("VEVENT")[0]["SUMMARY"])
    assert b"\r\nEND:VEVENT\r\nEND:VEVENT" not in response.content


def test_case_12_a_hide_through_the_service_removes_the_event_from_every_feed() -> None:
    client = Client()
    page = _event(10, title="Reported")
    staff = UserFactory.create(superuser=True)
    assert all(titles == ["Reported"] for titles in _all_titles(client, page.vendor).values())
    hide_event(staff, page, "Misleading")
    assert all(titles == [] for titles in _all_titles(client, page.vendor).values())
    assert client.get(f"{page.url}calendar.ics").status_code == 404


def test_the_single_event_ics_of_a_suspended_vendor_is_a_404() -> None:
    client = Client()
    page = _event(10, title="Soon suspended")
    assert client.get(f"{page.url}calendar.ics").status_code == 200
    vendor = page.vendor
    assert vendor is not None
    Vendor.objects.filter(pk=vendor.pk).update(status=Vendor.Status.SUSPENDED)
    assert client.get(f"{page.url}calendar.ics").status_code == 404


def test_the_rss_self_link_is_built_from_the_site_not_the_host_header(settings: Any) -> None:
    client = Client()
    _event(10)
    path = _index_path() + "feed.rss"
    settings.ALLOWED_HOSTS = ["*"]
    response = client.get(path, headers={"Host": "evil.example"})
    link = assert_valid_rss(response.content).find(f"{ATOM}link").get("href")
    assert "evil.example" not in link
    assert link.endswith(path)
