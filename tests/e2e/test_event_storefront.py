"""E2E (EVT.7): the "Upcoming events" section of a vendor storefront, with a fallback item in
another language, passes axe (WCAG 2.2 AA) at 360 and 1280 px with the CSP intact."""

import datetime as dt
from collections.abc import Callable
from pathlib import Path

import pytest
from django.utils import timezone, translation
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.catalog import services as catalog_services
from apps.cms.services import seed_site
from apps.core.i18n import primary_language
from apps.events.models import EventIndexPage
from apps.events.tests.factories import EventPageFactory
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations
from tests.i18n import other_language

pytestmark = pytest.mark.django_db(transaction=True)


def test_the_storefront_events_section_passes_axe_and_csp(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    seed_site()
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    foreign = other_language()
    start = (timezone.now() + dt.timedelta(days=3)).replace(minute=0, second=0, microsecond=0)
    for language, title, offset in ((primary_language(), "Local fair", 0), (foreign, "Messe", 2)):
        event = EventPageFactory.create(
            parent=EventIndexPage.objects.get(locale__language_code=language),
            vendor=vendor,
            title=title,
            starts_at=start + dt.timedelta(days=offset),
            ends_at=start + dt.timedelta(days=offset, hours=4),
            located=True,
        )
        event.save_revision().publish()
    language = primary_language()
    anchor = catalog_services.vendor_anchor(vendor.slug, language)
    assert anchor is not None
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    for width in (360, 1280):
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(f"{live_server.url}{anchor.paths[language]}")
        section = page.locator("section.vendor-events")
        expect(section).to_be_visible()
        fallback = section.locator("article.event-summary[lang]")
        expect(fallback).to_have_count(1)
        name = str(translation.get_language_info(foreign)["name_local"])
        expect(fallback.locator(".event-language")).to_have_text(name)
        shot(f"storefront-events-{width}")
        found = run_axe(page, include="section.vendor-events")
        assert found == [], f"storefront events at {width}px: {format_violations(found)}"
        assert csp_violations(page) == []
