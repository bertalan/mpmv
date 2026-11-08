"""E2E (EVT.3 part 2/2): a moderator approves a submitted event from the "Events awaiting review"
list in the Wagtail admin, staff hide it with a reason, and its vendor cannot resubmit it. The
admin pages show no Content-Security-Policy violation.
"""

import datetime as dt
import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from django.contrib.auth.models import Group
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import totp_token
from apps.core.models import AuditLog
from apps.events.models import EVENT_MODERATORS, EventPage
from apps.events.services import (
    EventRefused,
    ensure_event_review_workflow,
    save_event_draft,
    submit_event,
)
from apps.events.tests.factories import STARTS_AT
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations
from tests.e2e.login import verify_with_totp

pytestmark = pytest.mark.django_db(transaction=True)


def _axe_helper_style(violation: str) -> bool:
    # Wagtail's built-in accessibility checker adds a helper <style> the CSP blocks (CMS.1).
    return "style-src-elem blocked inline" in violation and "wagtailadmin/js/vendor.js" in violation


def _admin_violations(page: Page) -> list[str]:
    return [v for v in csp_violations(page) if not _axe_helper_style(v)]


def _submitted_event() -> tuple[EventPage, object]:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    owner = VendorMemberFactory.create(vendor=vendor).user
    data = {
        "title": "Spring trade fair",
        "summary": "Meet the makers at the spring trade fair.",
        "event_type": EventPage.EventType.FAIR,
        "starts_at": STARTS_AT,
        "ends_at": STARTS_AT + dt.timedelta(hours=8),
        "timezone": "Europe/Rome",
        "attendance": EventPage.Attendance.IN_PERSON,
        "venue_name": "Exhibition centre",
        "city": "Milano",
        "country": "IT",
    }
    event = save_event_draft(owner, data, vendor=vendor).page
    assert event is not None
    submit_event(owner, event)
    return EventPage.objects.get(pk=event.pk), owner


def test_a_moderator_approves_staff_hide_and_the_vendor_cannot_resubmit(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    ensure_event_review_workflow()
    event, owner = _submitted_event()
    assert event.status == EventPage.Status.PENDING
    password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    staff = UserFactory.create(email="moderator@example.com", password=password, superuser=True)
    staff.groups.add(Group.objects.get(name=EVENT_MODERATORS))
    device = TOTPDeviceFactory.create(user=staff)
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    page.goto(f"{live_server.url}/admin/login/")
    page.locator("input[name=username]").fill("moderator@example.com")
    page.locator("input[name=password]").fill(password)
    page.get_by_role("button", name="Sign in").click()
    verify_with_totp(page, totp_token(device))

    # The moderator opens the review list and approves the submitted event.
    page.goto(f"{live_server.url}/admin/events/review/")
    expect(page.get_by_role("heading", name="Events awaiting review")).to_be_visible()
    shot("review-list")
    assert _admin_violations(page) == [], "\n".join(_admin_violations(page))
    page.get_by_role("link", name="Spring trade fair").click()
    approve = page.get_by_role("button", name="Approve and Publish", exact=True)
    if not approve.is_visible():
        page.get_by_role("button", name="More actions").click()
    approve.click()
    expect(page.locator(".messages")).to_contain_text("Spring trade fair")
    event.refresh_from_db()
    assert event.live
    assert event.status == EventPage.Status.PUBLISHED

    # Staff hide it from the page editor, with a reason (an empty one is refused).
    page.goto(f"{live_server.url}/admin/pages/{event.pk}/edit/")
    page.get_by_role("button", name="More actions").click()
    page.get_by_role("link", name="Hide event").click()
    page.get_by_role("button", name="Hide event").click()  # the browser asks for the reason
    expect(page.get_by_label("Reason")).to_be_focused()
    page.get_by_label("Reason").fill("The offer in the description is misleading.")
    shot("hide-form")
    assert _admin_violations(page) == [], "\n".join(_admin_violations(page))
    page.get_by_role("button", name="Hide event").click()
    expect(page.locator(".messages")).to_contain_text("hidden")
    shot("hidden")
    event.refresh_from_db()
    assert event.hidden_at is not None
    assert not event.live
    assert event.status == EventPage.Status.HIDDEN
    assert AuditLog.objects.filter(action="events.event_hidden").count() == 1

    # The vendor cannot open it in the admin, nor resubmit it.
    page.context.clear_cookies()
    vendor_password = secrets.token_urlsafe(16)
    owner.set_password(vendor_password)
    owner.save()
    vendor_device = TOTPDeviceFactory.create(user=owner)
    page.goto(f"{live_server.url}/admin/login/")
    page.locator("input[name=username]").fill(owner.email)
    page.locator("input[name=password]").fill(vendor_password)
    page.get_by_role("button", name="Sign in").click()
    if page.get_by_label("Authentication code").count():
        verify_with_totp(page, totp_token(vendor_device))
    page.goto(f"{live_server.url}/admin/pages/{event.pk}/edit/")
    expect(page.get_by_role("button", name="Submit to Event review")).to_have_count(0)
    expect(page.locator("#id_title")).to_have_count(0)
    shot("vendor-refused")
    with pytest.raises(EventRefused):
        submit_event(owner, EventPage.objects.get(pk=event.pk))
    event.refresh_from_db()
    assert event.hidden_at is not None
    assert not event.live
