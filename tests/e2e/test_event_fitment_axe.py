"""E2E (FIT.11): the garage badge on the event list and on an event page passes axe (WCAG 2.2 AA)
at 360, 768 and 1280 px with no Content-Security-Policy violation. The badge is text, not colour."""

import datetime as dt

import pytest
from django.http import HttpResponse
from django.utils import timezone
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.events.tests.factories import EventFitmentFactory, EventPageFactory
from apps.fitment import garage
from apps.fitment.tests.factories import FitmentNodeFactory, FitmentSchemeFactory
from apps.fitment.views import _write
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)

WIDTHS = (360, 768, 1280)


def test_the_garage_badge_passes_axe_and_csp(page: Page, live_server: LiveServer) -> None:
    FitmentSchemeFactory.create()
    make = FitmentNodeFactory.create(name="Acme")
    roadster = FitmentNodeFactory.create(name="Roadster", parent=make)
    start = (timezone.now() + dt.timedelta(days=4)).replace(minute=0, second=0, microsecond=0)
    event = EventPageFactory.create(
        title="Roadster meet", starts_at=start, ends_at=start + dt.timedelta(hours=8)
    )
    event.save_revision().publish()
    event.refresh_from_db()
    EventFitmentFactory.create(page=event, node=roadster)
    value = garage.Garage((garage.Vehicle("Acme", "Roadster"),), 0)
    cookie = _write(HttpResponse(), value).cookies[garage.COOKIE_NAME].value
    page.context.add_cookies(
        [{"name": garage.COOKIE_NAME, "value": cookie, "url": live_server.url}]
    )
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    for width in WIDTHS:
        page.set_viewport_size({"width": width, "height": 900})
        for url in (f"{live_server.url}/events/", f"{live_server.url}{event.url}"):
            page.goto(url)
            expect(page.locator(".event-fitment").first).to_be_visible()
            violations = run_axe(page)
            assert not violations, f"{url} at {width}px: {format_violations(violations)}"
            assert csp_violations(page) == []
