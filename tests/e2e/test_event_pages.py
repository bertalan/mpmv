"""E2E (EVT.1 part 2/2): a staff editor creates and publishes an event in the Wagtail admin, and
the public page shows it on the base theme with no Content-Security-Policy violation.
"""

import re
import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import totp_token
from apps.events.models import EventIndexPage, EventPage
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations
from tests.e2e.login import verify_with_totp

pytestmark = pytest.mark.django_db(transaction=True)


def _axe_helper_style(violation: str) -> bool:
    # Wagtail's built-in accessibility checker adds a helper <style> the CSP blocks (CMS.1).
    return "style-src-elem blocked inline" in violation and "wagtailadmin/js/vendor.js" in violation


def test_staff_publishes_an_event_and_visitors_see_it(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    user = UserFactory.create(email="editor@example.com", password=password, superuser=True)
    device = TOTPDeviceFactory.create(user=user)
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    page.goto(f"{live_server.url}/admin/login/")
    page.locator("input[name=username]").fill("editor@example.com")
    page.locator("input[name=password]").fill(password)
    page.get_by_role("button", name="Sign in").click()
    verify_with_totp(page, totp_token(device))

    index = EventIndexPage.objects.get(locale__language_code="en")
    page.goto(f"{live_server.url}/admin/pages/add/events/eventpage/{index.pk}/")
    page.locator("#id_title").fill("Spring trade fair")
    page.locator("#id_summary").fill("Meet the makers at the spring trade fair.")
    page.locator("#id_event_type").select_option(EventPage.EventType.FAIR)
    page.locator("#id_starts_at").fill("2027-03-10 09:00")
    page.locator("#id_ends_at").fill("2027-03-10 17:00")
    page.locator("#id_attendance").select_option(EventPage.Attendance.IN_PERSON)
    page.locator("#id_venue_name").fill("Exhibition centre")
    page.locator("#id_street").fill("Via Esempio 1")
    page.locator("#id_postcode").fill("20121")
    page.locator("#id_city").fill("Milano")
    page.locator("#id_country").fill("IT")
    # An open date picker stays over the footer actions and swallows the Publish click: close
    # them with a click outside, as a user would.
    page.get_by_text("Shown in listings, feeds and search results.").click()
    expect(page.locator(".xdsoft_datetimepicker:visible")).to_have_count(0)
    shot("admin-add-event")
    page.get_by_role("button", name="More actions").click()
    page.get_by_role("button", name="Publish").click()
    expect(page.get_by_text("Page 'Spring trade fair' created and published.")).to_be_visible()
    admin_violations = [v for v in csp_violations(page) if not _axe_helper_style(v)]
    assert admin_violations == [], "\n".join(admin_violations)

    event = EventPage.objects.get(title="Spring trade fair")
    assert event.live
    assert event.status == EventPage.Status.PUBLISHED

    page.goto(f"{live_server.url}{event.url}")
    expect(page).to_have_url(re.compile(r"/events/spring-trade-fair/$"))
    expect(page.get_by_role("heading", level=1)).to_have_text("Spring trade fair")
    expect(page.locator("time").first).to_have_attribute("datetime", "2027-03-10T09:00:00+01:00")
    expect(page.locator("address")).to_contain_text("Milano")
    shot("public-event")
    assert csp_violations(page) == [], "\n".join(csp_violations(page))

    page.goto(f"{live_server.url}{index.url}")
    expect(page.get_by_role("link", name="Spring trade fair")).to_be_visible()
    shot("public-index")

    assert csp_violations(page) == [], "\n".join(csp_violations(page))
