"""EVT.9 part 2: feed discovery (`<link rel="alternate">`), the Subscribe block and the feed
URLs in `llms.txt` (spec 15 "Discovery"). Absolute URLs, no inline script or style."""

import datetime as dt
import re
from typing import Any

import pytest
from django.apps import apps
from django.template.loader import render_to_string
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
from apps.themes.services import use_theme
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

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


def test_ride_renders_the_same_discovery(client: Client) -> None:
    index = primary_index()
    with use_theme("ride"):
        html = render_to_string("events/_feed_alternates.html", {"links": _links_for(index)})
        block = render_to_string("events/_subscribe.html", {"links": _links_for(index)})
    assert "application/rss+xml" in html
    assert "text/calendar" in html
    assert "webcal://" in block


def _links_for(index: Any) -> Any:
    from apps.events import discovery

    links = discovery.feed_links(index.locale.language_code)
    assert links is not None
    return links


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
