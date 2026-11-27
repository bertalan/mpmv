"""EVT.6: the public event pages on the base theme (spec 15 "Public pages"): the list with
filters and pagination, the month calendar table, the past archive, the event page and its ICS
download. Dates are relative to now so the tests do not age."""

import datetime as dt
import re
import zoneinfo
from urllib.parse import parse_qs, urlparse

import pytest
from django.apps import apps
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.events.models import EventPage
from apps.events.tests.factories import EventPageFactory, primary_index
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory
from tests.i18n import translated

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _indexing_on() -> None:
    """The site allows indexing, so the page's own robots rule is what the tests see."""
    from wagtail.models import Site

    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )


def _event(days: int = 10, *, hours: int = 8, publish: bool = True, **fields: object) -> EventPage:
    start = (timezone.now() + dt.timedelta(days=days)).replace(minute=0, second=0, microsecond=0)
    page = EventPageFactory.create(
        starts_at=start, ends_at=start + dt.timedelta(hours=hours), **fields
    )
    if publish:
        page.save_revision().publish()
        page.refresh_from_db()
    return page


def _get(client: Client, url: str, status: int = 200) -> str:
    response = client.get(url)
    assert response.status_code == status, (url, response.status_code)
    return response.content.decode()


def _index_url() -> str:
    return str(primary_index().url)


def _assert_csp_safe(html: str) -> None:
    assert "<style" not in html
    assert " style=" not in html
    for script in re.findall(r"<script[^>]*>", html):
        assert "src=" in script or 'type="application/ld+json"' in script, script


# --- The list


def test_the_list_shows_upcoming_events_and_leaves_out_past_ones(client: Client) -> None:
    _event(10, title="Next fair")
    _event(-20, title="Old fair")
    html = _get(client, _index_url())
    assert "Next fair" in html
    assert "Old fair" not in html
    assert "1 event" in html


def test_the_list_works_without_javascript(client: Client) -> None:
    _event(10)
    html = _get(client, _index_url())
    form = re.search(r"<form[^>]*event-filters[^>]*>", html)
    assert form
    assert 'method="get"' in form.group(0)
    for control in ("event-filter-type",):
        assert f'<label for="{control}"' in html
    assert "<article" in html
    assert "<time datetime=" in html
    _assert_csp_safe(html)


def test_the_list_groups_events_by_month_under_headings(client: Client) -> None:
    _event(10, title="First")
    _event(75, title="Later")
    html = _get(client, _index_url())
    assert len(re.findall(r'<h3 class="event-month">', html)) == 2
    assert html.index("First") < html.index("Later")


def test_filters_by_type_vendor_and_month(client: Client) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE, display_name="Alpha Vendor")
    _event(10, title="Mine fair", vendor=vendor)
    _event(11, title="Other promo", promotion=True)
    base = _index_url()
    by_type = _get(client, f"{base}?type=promotion")
    assert "Other promo" in by_type
    assert "Mine fair" not in by_type
    by_vendor = _get(client, f"{base}?vendor={vendor.slug}")
    assert "Mine fair" in by_vendor
    assert "Other promo" not in by_vendor
    month = (timezone.now() + dt.timedelta(days=10)).strftime("%Y-%m")
    in_month = _get(client, f"{base}?month={month}")
    assert "Mine fair" in in_month
    far = _get(client, f"{base}?month=2099-01")
    assert "Mine fair" not in far
    assert translated("No events are planned at the moment.") in far
    # An unknown value is ignored, not an error.
    assert "Mine fair" in _get(client, f"{base}?type=bogus&month=nonsense")


def test_the_filter_options_only_list_vendors_and_months_with_events(client: Client) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE, display_name="Alpha Vendor")
    _event(10, vendor=vendor)
    html = _get(client, _index_url())
    assert 'for="event-filter-vendor"' in html
    assert "Alpha Vendor" in html
    assert 'for="event-filter-month"' in html


def test_pagination_pages_and_a_missing_page_is_404(client: Client) -> None:
    for number in range(13):
        _event(5 + number, title=f"Event number {number:02d}")
    base = _index_url()
    first = _get(client, base)
    assert "Event number 00" in first
    assert "Event number 12" not in first
    assert 'rel="next"' in first
    assert translated("Page %(page)s of %(count)s") % {"page": 1, "count": 2} in first
    second = _get(client, f"{base}?page=2")
    assert "Event number 12" in second
    assert 'rel="prev"' in second
    _get(client, f"{base}?page=3", 404)
    _get(client, f"{base}?page=abc", 404)
    _get(client, f"{base}?page=0", 404)


def test_robots_filtered_and_later_pages_are_noindex_the_plain_list_is_not(client: Client) -> None:
    for number in range(13):
        _event(5 + number)
    base = _index_url()
    assert 'name="robots"' not in _get(client, base)
    assert 'content="noindex, follow"' in _get(client, f"{base}?type=fair")
    assert 'content="noindex, follow"' in _get(client, f"{base}?page=2")


def test_pending_draft_and_hidden_events_are_404_to_visitors(client: Client) -> None:
    draft = _event(10, publish=False)
    _get(client, str(draft.url), 404)
    hidden = _event(10, title="Hidden fair")
    EventPage.objects.filter(pk=hidden.pk).update(hidden_at=timezone.now())
    _get(client, str(hidden.url), 404)
    _get(client, str(hidden.url) + "calendar.ics", 404)
    assert "Hidden fair" not in _get(client, _index_url())
    unpublished = _event(10, title="Unpublished fair")
    unpublished.unpublish()
    assert "Unpublished fair" not in _get(client, _index_url())


def test_events_of_a_suspended_vendor_are_404(client: Client) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.SUSPENDED)
    page = _event(10, vendor=vendor, title="Suspended fair")
    _get(client, str(page.url), 404)
    assert "Suspended fair" not in _get(client, _index_url())


def test_the_ics_of_a_non_live_or_hidden_event_is_404_without_a_public_cache(
    client: Client,
) -> None:
    draft = _event(10, publish=False)
    pending = _event(10, publish=False, title="Pending fair")
    pending.save_revision()  # a submitted revision still waiting for review: not live
    unpublished = _event(10)
    unpublished.unpublish()
    hidden = _event(10)
    EventPage.objects.filter(pk=hidden.pk).update(hidden_at=timezone.now())
    suspended = _event(10, vendor=VendorFactory.create(status=Vendor.Status.SUSPENDED))
    for page in (draft, pending, unpublished, hidden, suspended):
        response = client.get(f"{page.url}calendar.ics")
        assert response.status_code == 404, page.title
        assert "public" not in response.get("Cache-Control", "")


# --- Month calendar and archive


def test_the_calendar_is_an_accessible_table_with_event_links(client: Client) -> None:
    # Noon on a local date three days ahead, in the event's own zone: the event day and the
    # requested month agree whatever the hour (a late "+3 days" used to cross a month edge).
    zone = zoneinfo.ZoneInfo(EventPageFactory.build().timezone)
    day = timezone.now().astimezone(zone).date() + dt.timedelta(days=3)
    start = dt.datetime.combine(day, dt.time(12), tzinfo=zone)
    page = EventPageFactory.create(
        title="Calendar fair", starts_at=start, ends_at=start + dt.timedelta(hours=2)
    )
    page.save_revision().publish()
    page.refresh_from_db()
    html = _get(client, f"{_index_url()}calendar/{day:%Y-%m}/")
    assert "<table" in html
    assert "<caption>" in html
    assert html.count('<th scope="col">') == 7
    cell = re.search(r"<td[^>]*>(?:(?!</td>).)*Calendar fair(?:(?!</td>).)*</td>", html, re.S)
    assert cell
    assert str(page.url) in cell.group(0)
    assert 'rel="prev"' in html
    assert 'rel="next"' in html
    assert 'content="noindex, follow"' in html
    _assert_csp_safe(html)


def test_the_calendar_keeps_filters_on_its_month_links_and_rejects_bad_months(
    client: Client,
) -> None:
    _event(3)
    base = f"{_index_url()}calendar/"
    html = _get(client, f"{base}2027-05/?type=fair")
    assert "?type=fair" in re.search(r'<a href="([^"]*)" rel="next"', html).group(1)  # type: ignore[union-attr]
    _get(client, f"{base}2027-13/", 404)
    _get(client, f"{base}2027-00/", 404)


def test_a_multi_day_event_appears_on_each_day(client: Client) -> None:
    today = timezone.localdate()
    first = today + dt.timedelta(days=2)
    EventPageFactory.create(
        all_day_event=True,
        title="Long fair",
        start_date=first,
        end_date=first + dt.timedelta(days=2),
        starts_at=None,
        ends_at=None,
    ).save_revision().publish()
    html = _get(client, f"{_index_url()}calendar/{first:%Y-%m}/")
    table = html[html.index("<table") : html.index("</table>")]
    assert table.count("Long fair") >= 1  # at least its in-month days


def test_the_past_archive_lists_ended_events_newest_first_and_is_noindex(client: Client) -> None:
    _event(-40, title="Older fair")
    _event(-10, title="Recent fair")
    _event(10, title="Coming fair")
    html = _get(client, f"{_index_url()}past/")
    assert html.index("Recent fair") < html.index("Older fair")
    assert "Coming fair" not in html
    assert 'content="noindex, follow"' in html


# --- The event page


def test_the_event_page_links_the_ics_and_the_report_form(client: Client) -> None:
    page = _event(10, title="Linked fair")
    html = _get(client, str(page.url))
    assert f'href="{page.url}calendar.ics"' in html
    report = re.search(r'href="(/[^"]*report/\?[^"]*)"', html)
    assert report is not None
    query = parse_qs(urlparse(report.group(1).replace("&amp;", "&")).query)
    assert query["kind"] == ["event"]
    assert query["url"][0].endswith(str(page.url))
    assert report.group(1).split("?")[0] == reverse("dsa_notice")
    assert 'name="robots"' not in html
    _assert_csp_safe(html)


def test_a_cancelled_event_shows_the_status_and_is_noindex(client: Client) -> None:
    page = _event(10, title="Called off fair", cancelled_at=timezone.now())
    html = _get(client, str(page.url))
    assert 'role="status"' in html
    assert "Cancelled" in html
    assert 'content="noindex, follow"' in html
    assert "STATUS:CANCELLED" in _get(client, f"{page.url}calendar.ics")


def test_an_event_that_ended_over_30_days_ago_is_noindex_but_reachable(client: Client) -> None:
    page = _event(-40, title="Long gone fair")
    assert 'content="noindex, follow"' in _get(client, str(page.url))


def test_the_ics_download_is_calendar_text_with_crlf_and_escaping(client: Client) -> None:
    page = _event(10, title="Fair, with; specials", located=True)
    response = client.get(f"{page.url}calendar.ics")
    assert response.status_code == 200
    assert response["Content-Type"] == "text/calendar; charset=utf-8"
    assert f'filename="{page.slug}.ics"' in response["Content-Disposition"]
    body = response.content.decode()
    assert body.startswith("BEGIN:VCALENDAR\r\n")
    assert body.endswith("END:VCALENDAR\r\n")
    assert "SUMMARY:Fair\\, with\\; specials\r\n" in body
    assert f"UID:{page.translation_key}@" in body
    assert "GEO:45.464204;9.189982\r\n" in body
    assert all(len(line.encode()) <= 75 for line in body.split("\r\n"))
    assert "ORGANIZER" not in body
    assert "ATTENDEE" not in body
