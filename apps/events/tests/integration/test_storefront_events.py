"""EVT.7: the "Upcoming events" section of the vendor storefront (spec 15 "Storefront").

The section lists the vendor's next three public events with the language rule of EVT.8 (the
translation in the page language, else the original-language page with `lang`, `hreflang` and
the visible language name), links to the vendor's events, and follows publish, cancel and hide
on the next origin response (the storefront sends no shared-cache lifetime, so nothing keeps a
stale copy).
"""

import datetime as dt
import re
from collections.abc import Callable

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone, translation
from wagtail.models import Locale

from apps.accounts.tests.factories import UserFactory
from apps.catalog import services as catalog_services
from apps.cms.services import seed_site
from apps.core.i18n import primary_language
from apps.events import services
from apps.events.models import EventIndexPage, EventPage
from apps.events.tests.factories import EventPageFactory
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory
from tests.i18n import other_language

pytestmark = pytest.mark.django_db

SECTION = re.compile(r'<section class="vendor-events".*?</section>', re.S)


@pytest.fixture(autouse=True)
def _site() -> None:
    seed_site()


def _storefront(client: Client, vendor: Vendor) -> str:
    language = primary_language()
    anchor = catalog_services.vendor_anchor(vendor.slug, language)
    assert anchor is not None
    response = client.get(anchor.paths[language])
    assert response.status_code == 200
    return response.content.decode()


def _section(html: str) -> str:
    found = SECTION.search(html)
    assert found, "no events section"
    return found.group(0)


def _event(vendor: Vendor, title: str, days: int, *, language: str | None = None) -> EventPage:
    parent = (
        EventIndexPage.objects.get(locale__language_code=language)
        if language
        else EventIndexPage.objects.get(locale__language_code=primary_language())
    )
    start = (timezone.now() + dt.timedelta(days=days)).replace(minute=0, second=0, microsecond=0)
    page = EventPageFactory.create(
        parent=parent,
        vendor=vendor,
        title=title,
        starts_at=start,
        ends_at=start + dt.timedelta(hours=4),
    )
    page.save_revision().publish()
    page.refresh_from_db()
    return page


def test_the_storefront_shows_the_next_three_events_of_the_vendor(client: Client) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    other = VendorFactory.create(status=Vendor.Status.ACTIVE)
    for number, days in enumerate((9, 3, 6, 12), start=1):
        _event(vendor, f"Mine {number}", days)
    _event(other, "Theirs", 1)
    section = _section(_storefront(client, vendor))
    assert section.index("Mine 2") < section.index("Mine 3") < section.index("Mine 1")
    assert "Mine 4" not in section  # the fourth is beyond the next three
    assert "Theirs" not in section
    index = EventIndexPage.objects.get(locale__language_code=primary_language())
    assert f'href="{index.url}?vendor={vendor.slug}"' in section


def test_a_vendor_without_events_has_no_section(client: Client) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    assert "vendor-events" not in _storefront(client, vendor)


def test_an_event_in_another_language_carries_lang_hreflang_and_the_language_name(
    client: Client,
) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    foreign = other_language()
    _event(vendor, "Fremdmesse", 5, language=foreign)
    section = _section(_storefront(client, vendor))
    assert f'<article class="event-summary" lang="{foreign}">' in section
    assert f'hreflang="{foreign}"' in section
    name = str(translation.get_language_info(foreign)["name_local"])
    assert f'<span class="event-language" lang="{foreign}">{name}</span>' in section


def test_a_translation_in_the_page_language_wins_over_the_original(client: Client) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    foreign = other_language()
    original = _event(vendor, "Originalmesse", 5, language=foreign)
    staff = UserFactory.create(is_staff=True, is_superuser=True)
    translated = services.add_event_translation(
        staff,
        original,
        Locale.objects.get(language_code=primary_language()),
        {"title": "Fiera tradotta", "summary": "Testo."},
    ).page
    assert translated is not None
    translated.save_revision().publish()
    section = _section(_storefront(client, vendor))
    assert "Fiera tradotta" in section
    assert "Originalmesse" not in section
    assert "lang=" not in section.split("<article", 1)[1].split(">", 1)[0]


def test_hiding_cancelling_and_publishing_show_on_the_next_response(client: Client) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    keep = _event(vendor, "Stays here", 4)
    gone = _event(vendor, "Hidden soon", 2)
    staff = UserFactory.create(is_staff=True, is_superuser=True)
    assert "Hidden soon" in _storefront(client, vendor)  # the first response is not stored
    services.hide_event(staff, gone, "Test reason")
    html = _storefront(client, vendor)
    assert "Hidden soon" not in html
    assert "Stays here" in html
    anchor = catalog_services.vendor_anchor(vendor.slug, primary_language())
    assert anchor is not None
    response = client.get(anchor.paths[primary_language()])
    assert "max-age" not in response.get("Cache-Control", "")
    assert "s-maxage" not in response.get("Cache-Control", "")
    _event(vendor, "Newly published", 1)
    assert "Newly published" in _storefront(client, vendor)
    services.cancel_event(staff, keep)
    cancelled = _section(_storefront(client, vendor))
    assert "Cancelled" in cancelled  # a cancelled event stays, marked (spec 15)


def test_the_query_count_does_not_grow_with_the_number_of_events(client: Client) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    for number in range(3):
        _event(vendor, f"Event {number}", number + 1)
    _storefront(client, vendor)  # warm caches that are not per-event

    def count() -> int:
        with CaptureQueriesContext(connection) as queries:
            _storefront(client, vendor)
        return len(queries)

    few = count()
    for number in range(9):
        _event(vendor, f"More {number}", number + 10)
    assert count() == few


def _draft(vendor: Vendor) -> EventPage:
    page = _event(vendor, "Hidden one", 3)
    page.unpublish()
    return page


def _pending(vendor: Vendor) -> EventPage:
    page = _event(vendor, "Hidden one", 3)
    page.live = False
    page.save()
    EventPage.objects.filter(pk=page.pk).update(status="pending")
    return page


def _past(vendor: Vendor) -> EventPage:
    return _event(vendor, "Hidden one", -5)


@pytest.mark.parametrize("make", [_draft, _pending, _past])
def test_the_storefront_never_shows_a_draft_pending_or_past_event(
    client: Client, make: Callable[[Vendor], EventPage]
) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    make(vendor)
    assert "Hidden one" not in _storefront(client, vendor)


def test_a_suspended_vendors_events_are_not_shown(client: Client) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    _event(vendor, "Hidden one", 3)
    Vendor.objects.filter(pk=vendor.pk).update(status=Vendor.Status.SUSPENDED)
    vendor.refresh_from_db()
    anchor = catalog_services.vendor_anchor(vendor.slug, primary_language())
    if anchor is None:
        return  # the storefront itself is gone for a suspended vendor
    response = client.get(anchor.paths[primary_language()])
    assert "Hidden one" not in response.content.decode()


def test_no_locale_leaves_the_section_empty() -> None:
    from apps.events.templatetags.events_tags import storefront_events

    assert storefront_events(VendorFactory.create(), "xx")["items"] == []
