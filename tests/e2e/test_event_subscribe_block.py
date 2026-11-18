"""E2E (EVT.9 follow-up): the Subscribe block passes axe (WCAG 2.2 AA) with no
Content-Security-Policy violation, in both themes (`base` and `ride`) at 360, 768 and 1280 px."""

import datetime as dt

import pytest
from django.utils import timezone
from playwright.sync_api import Page, expect
from pytest_django.fixtures import Settings as SettingsWrapper
from pytest_django.live_server_helper import LiveServer

from apps.events.tests.factories import EventPageFactory
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)

BLOCK = ".event-subscribe"


@pytest.mark.parametrize("theme", ["base", "ride"])
@pytest.mark.parametrize("width", [360, 768, 1280])
def test_the_subscribe_block_is_accessible_and_csp_clean(
    theme: str,
    width: int,
    page: Page,
    live_server: LiveServer,
    settings: SettingsWrapper,
) -> None:
    settings.MPMV_THEME_DEFAULT = theme
    start = (timezone.now() + dt.timedelta(days=4)).replace(minute=0, second=0, microsecond=0)
    event = EventPageFactory.create(starts_at=start, ends_at=start + dt.timedelta(hours=8))
    event.save_revision().publish()
    event.refresh_from_db()
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    page.set_viewport_size({"width": width, "height": 900})
    for path in ("/events/", str(event.url)):
        page.goto(f"{live_server.url}{path}")
        expect(page.locator("html")).to_have_attribute("data-theme", theme)
        expect(page.locator(f"{BLOCK} a[href^='webcal://']")).to_be_visible()
        expect(page.locator(f"{BLOCK} input[readonly]")).to_be_visible()
        found = run_axe(page, include=BLOCK)
        assert found == [], f"{theme} {path} at {width}px: {format_violations(found)}"
        assert csp_violations(page) == []
