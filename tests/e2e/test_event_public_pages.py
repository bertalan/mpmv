"""E2E (EVT.6): the list, the month view, the archive and an event page pass axe (WCAG 2.2 AA)
at 360, 768 and 1280 px with no Content-Security-Policy violation, and the list works as a plain
GET form (the filter is applied by a form submission, no script involved)."""

import datetime as dt
import re
from collections.abc import Callable
from pathlib import Path

import pytest
from django.utils import timezone
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.events.tests.factories import EventPageFactory
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations
from tests.i18n import translated

pytestmark = pytest.mark.django_db(transaction=True)

WIDTHS = (360, 768, 1280)


def _publish(**fields: object) -> str:
    page = EventPageFactory.create(**fields)
    page.save_revision().publish()
    page.refresh_from_db()
    return str(page.url)


def test_event_pages_pass_axe_and_csp_at_three_widths(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    start = (timezone.now() + dt.timedelta(days=4)).replace(minute=0, second=0, microsecond=0)
    fair = _publish(
        title="Spring fair", starts_at=start, ends_at=start + dt.timedelta(hours=8), located=True
    )
    _publish(
        title="Autumn sale",
        promotion=True,
        starts_at=start + dt.timedelta(days=40),
        ends_at=start + dt.timedelta(days=41),
    )
    _publish(
        title="Called off meeting",
        cancelled_at=timezone.now(),
        starts_at=start + dt.timedelta(days=2),
        ends_at=start + dt.timedelta(days=2, hours=2),
    )
    _publish(
        title="Last year fair",
        starts_at=start - dt.timedelta(days=60),
        ends_at=start - dt.timedelta(days=60) + dt.timedelta(hours=8),
    )
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    base = live_server.url
    urls = {
        "list": f"{base}/events/",
        "month": f"{base}/events/calendar/{start:%Y-%m}/",
        "past": f"{base}/events/past/",
        "event": f"{base}{fair}",
    }
    for width in WIDTHS:
        page.set_viewport_size({"width": width, "height": 900})
        for name, url in urls.items():
            page.goto(url)
            expect(page.get_by_role("heading", level=1)).to_be_visible()
            if name in ("list", "event"):
                expect(page.locator(".event-subscribe a[href^='webcal://']")).to_be_visible()
            shot(f"event-{name}-{width}")
            found = run_axe(page)
            assert found == [], f"{name} at {width}px: {format_violations(found)}"
            assert csp_violations(page) == []


def test_the_list_filters_with_a_plain_form_submission(page: Page, live_server: LiveServer) -> None:
    start = timezone.now() + dt.timedelta(days=4)
    _publish(title="Spring fair", starts_at=start, ends_at=start + dt.timedelta(hours=8))
    _publish(
        title="Autumn sale",
        promotion=True,
        starts_at=start + dt.timedelta(days=1),
        ends_at=start + dt.timedelta(days=1, hours=8),
    )
    page.goto(f"{live_server.url}/events/")
    page.get_by_label(translated("Type")).select_option("promotion")
    page.get_by_role("button", name=translated("Show events")).click()
    expect(page).to_have_url(re.compile(r"/events/\?.*type=promotion"))
    expect(page.get_by_role("link", name="Autumn sale")).to_be_visible()
    expect(page.get_by_role("link", name="Spring fair")).to_have_count(0)
