"""EVT.9 part 2: feed discovery (`<link rel="alternate">`), the Subscribe block and the feed
URLs in `llms.txt` (spec 15 "Discovery"). Absolute URLs, no inline script or style."""

import datetime as dt
import re
from typing import Any

import pytest
from django.apps import apps
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from wagtail.models import Site

from apps.catalog import slugs
from apps.cms.services import seed_site
from apps.core.i18n import primary_language
from apps.core.site_url import site_root_url
from apps.events.models import EventPage
from apps.events.tests.factories import EventPageFactory, primary_index
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory
from tests.i18n import language_prefix, other_language, vendor_url

pytestmark = pytest.mark.django_db

RSS_LINK = re.compile(
    r'<link rel="alternate" type="application/rss\+xml" title="([^"]*)" href="([^"]+)"'
)
ICS_LINK = re.compile(r'<link rel="alternate" type="text/calendar" title="([^"]*)" href="([^"]+)"')


@pytest.fixture(autouse=True)
def _site() -> None:
    seed_site()
    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )


def _event(**fields: Any) -> EventPage:
    start = (timezone.now() + dt.timedelta(days=10)).replace(minute=0, second=0, microsecond=0)
    page = EventPageFactory.create(starts_at=start, ends_at=start + dt.timedelta(hours=8), **fields)
    page.save_revision().publish()
    page.refresh_from_db()
    return page


def _get(client: Client, url: str) -> str:
    response = client.get(url)
    assert response.status_code == 200, (url, response.status_code)
    return response.content.decode()


def _links(html: str) -> tuple[str, str]:
    rss, ics = RSS_LINK.search(html), ICS_LINK.search(html)
    assert rss is not None, "no RSS alternate link"
    assert ics is not None, "no ICS alternate link"
    assert rss.group(1)  # a translated title
    assert ics.group(1)
    return rss.group(2), ics.group(2)


def _root() -> str:
    return site_root_url()


def test_the_index_and_the_month_view_and_the_past_page_carry_both_alternates(
    client: Client,
) -> None:
    index = str(primary_index().url)
    for url in (index, f"{index}calendar/{timezone.localdate():%Y-%m}/", f"{index}past/"):
        rss, ics = _links(_get(client, url))
        assert rss == f"{_root()}{index}feed.rss"
        assert ics == f"{_root()}{index}calendar.ics"


def test_the_type_filter_points_at_the_type_feeds(client: Client) -> None:
    index = str(primary_index().url)
    rss, ics = _links(_get(client, f"{index}?type=fair"))
    assert rss == f"{_root()}{index}type/fair/feed.rss"
    assert ics == f"{_root()}{index}type/fair/calendar.ics"


def test_the_vendor_filter_points_at_the_vendor_feeds(client: Client) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    index = str(primary_index().url)
    rss, ics = _links(_get(client, f"{index}?vendor={vendor.slug}"))
    assert rss == f"{_root()}{index}vendor/{vendor.slug}/feed.rss"
    assert ics == f"{_root()}{index}vendor/{vendor.slug}/calendar.ics"


def test_an_event_page_of_a_vendor_carries_the_vendor_feeds(client: Client) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    page = _event(vendor=vendor)
    index = str(primary_index().url)
    rss, ics = _links(_get(client, str(page.url)))
    assert rss == f"{_root()}{index}vendor/{vendor.slug}/feed.rss"
    assert ics == f"{_root()}{index}vendor/{vendor.slug}/calendar.ics"


def test_an_event_page_without_a_vendor_carries_the_platform_feeds(client: Client) -> None:
    page = _event(vendor=None)
    index = str(primary_index().url)
    rss, ics = _links(_get(client, str(page.url)))
    assert (rss, ics) == (f"{_root()}{index}feed.rss", f"{_root()}{index}calendar.ics")


def test_the_vendor_storefront_carries_both_alternates(client: Client) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    index = str(primary_index().url)
    path = slugs.vendor_path(primary_language(), vendor.slug, default_language=primary_language())
    rss, ics = _links(_get(client, path))
    assert rss == f"{_root()}{index}vendor/{vendor.slug}/feed.rss"
    assert ics == f"{_root()}{index}vendor/{vendor.slug}/calendar.ics"


def test_the_subscribe_block_offers_rss_ics_webcal_and_a_copy_link(client: Client) -> None:
    index = str(primary_index().url)
    html = _get(client, index)
    host = _root().split("://", 1)[1]
    assert 'class="event-subscribe"' in html
    assert f'href="webcal://{host}{index}calendar.ics"' in html
    assert f'href="{_root()}{index}feed.rss"' in html
    assert f'href="{_root()}{index}calendar.ics"' in html
    assert f'value="{_root()}{index}calendar.ics"' in html  # the https fallback to copy
    assert "<style" not in html
    assert " style=" not in html


def test_the_event_page_has_the_subscribe_block(client: Client) -> None:
    page = _event(vendor=None)
    html = _get(client, str(page.url))
    assert 'class="event-subscribe"' in html
    assert "webcal://" in html


def test_the_feeds_section_of_llms_txt_lists_the_feeds_of_the_language(client: Client) -> None:
    index = str(primary_index().url)
    text = client.get(reverse("llms_txt")).content.decode()
    assert "## Event feeds" in text
    assert f"]({_root()}{index}feed.rss)" in text
    assert f"]({_root()}{index}calendar.ics)" in text


def test_the_rss_item_of_a_vendor_event_carries_the_storefront_url(client: Client) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    _event(vendor=vendor)
    index = str(primary_index().url)
    body = _get(client, f"{index}feed.rss")
    shop = slugs.vendor_path(primary_language(), vendor.slug, default_language=primary_language())
    assert f"{_root()}{shop}" in body


# --- #640 review follow-ups (EVT.9)


def test_the_rss_and_ics_alternates_have_distinct_titles(client: Client) -> None:
    html = _get(client, str(primary_index().url))
    rss, ics = RSS_LINK.search(html), ICS_LINK.search(html)
    assert rss is not None
    assert ics is not None
    assert rss.group(1) != ics.group(1), "a feed reader lists both: the titles must differ"
    assert rss.group(1).endswith("(RSS)")
    assert ics.group(1).endswith("(ICS)")


def test_a_suspended_vendor_gets_no_alternates(client: Client) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.SUSPENDED)
    index = str(primary_index().url)
    html = _get(client, f"{index}?vendor={vendor.slug}")
    for match in (RSS_LINK.search(html), ICS_LINK.search(html)):
        assert match is None or f"vendor/{vendor.slug}/" not in match.group(2)


def test_a_foreign_host_header_still_gives_base_url_links(client: Client) -> None:
    index = str(primary_index().url)
    response = client.get(index, HTTP_HOST="evil.example")
    if response.status_code != 200:  # ALLOWED_HOSTS refused the host: nothing can leak
        return
    rss, ics = _links(response.content.decode())
    assert "evil.example" not in rss + ics
    assert rss.startswith(_root())


def test_the_feed_links_are_computed_once_per_request(
    client: Client, django_assert_max_num_queries: Any
) -> None:
    from django.template import Context, Template
    from django.test import RequestFactory

    request = RequestFactory().get("/")
    template = Template(
        "{% load event_feeds %}{% event_feed_alternates %}{% event_subscribe %}"
        "{% event_feed_alternates %}"
    )
    context = Context({"request": request})
    template.render(context)  # warm: one lookup
    with django_assert_max_num_queries(0):
        template.render(Context({"request": request}))


# --- #654 follow-ups (EVT.9): the remaining #640 minors


def _other_index() -> Any:
    """The live events index of another language (the seed creates one per language)."""
    from apps.events.models import EventIndexPage

    primary_index()
    index = EventIndexPage.objects.live().get(locale__language_code=other_language())
    return index


def test_the_vendor_feed_items_carry_the_shop_line_and_the_platform_feed_too(
    client: Client,
) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    _event(vendor=vendor)
    index = str(primary_index().url)
    shop = slugs.vendor_path(primary_language(), vendor.slug, default_language=primary_language())
    expected = f"{_root()}{shop}"
    vendor_feed = _get(client, f"{index}vendor/{vendor.slug}/feed.rss")
    assert f"Shop: {expected}" in vendor_feed
    # The line is part of every item of a vendor event, platform feed included (as reviewed).
    assert f"Shop: {expected}" in _get(client, f"{index}feed.rss")


def test_an_event_without_a_vendor_has_no_shop_line(client: Client) -> None:
    _event(vendor=None)
    index = str(primary_index().url)
    assert "Shop:" not in _get(client, f"{index}feed.rss")


def test_the_ics_description_stays_escaped_with_hostile_content(client: Client) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    hostile = "a,b;c\\d\nBEGIN:VEVENT\nATTENDEE:evil"
    _event(vendor=vendor, summary=hostile)
    index = str(primary_index().url)
    body = _get(client, f"{index}vendor/{vendor.slug}/calendar.ics")
    unfolded = body.replace("\r\n ", "")
    assert r"DESCRIPTION:a\,b\;c\\d\nBEGIN:VEVENT\nATTENDEE:evil" in unfolded
    lines = unfolded.split("\r\n")
    assert lines.count("BEGIN:VEVENT") == 1, "a newline in the text must not open a component"
    assert not any(line.startswith(("ATTENDEE", "BEGIN:VEVENT\\")) for line in lines)


def test_a_language_without_a_live_index_has_no_links(client: Client) -> None:
    from apps.events import discovery

    primary_index()
    other = other_language()
    _other_index().unpublish()
    assert discovery.feed_links(other) is None
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    response = client.get(vendor_url(other, vendor.slug))
    if response.status_code == 200:
        html = response.content.decode()
        assert RSS_LINK.search(html) is None
        assert ICS_LINK.search(html) is None
        assert 'class="event-subscribe"' not in html
    text = client.get(reverse("llms_language", kwargs={"language": other})).content.decode()
    assert "## Event feeds" not in text or ".rss" not in text


def test_the_feed_urls_of_a_non_primary_language_carry_its_prefix(client: Client) -> None:
    index = _other_index()
    prefix = str(index.url)
    assert prefix.startswith(language_prefix(other_language()) + "/")
    html = _get(client, prefix)
    rss, ics = _links(html)
    assert rss == f"{_root()}{prefix}feed.rss"
    assert ics == f"{_root()}{prefix}calendar.ics"
    host = _root().split("://", 1)[1]
    assert f'href="webcal://{host}{prefix}calendar.ics"' in html
    assert 'class="event-subscribe"' in html


def test_llms_txt_of_each_language_lists_that_languages_feeds(client: Client) -> None:
    other = other_language()
    index = _other_index()
    primary = str(primary_index().url)
    for language, own, foreign in (
        (primary_language(), primary, str(index.url)),
        (other, str(index.url), primary),
    ):
        text = client.get(reverse("llms_language", kwargs={"language": language})).content.decode()
        assert f"]({_root()}{own}feed.rss)" in text
        assert f"]({_root()}{own}calendar.ics)" in text
        assert f"]({_root()}{foreign}feed.rss)" not in text
        assert f"]({_root()}{foreign}calendar.ics)" not in text
    assert other != primary_language()
