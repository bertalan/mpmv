"""E2E (EVT.4 part 1): a vendor creates an event in the Manager and submits it, with the keyboard.

The form is filled field by field and the buttons are pressed with Enter. The body blocks, the
map picker and the moderator's approval belong to parts 2 and 3. Pages pass axe and the strict
CSP. Screenshots are kept for review.
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
from apps.events.models import EventPage
from apps.events.tests.factories import EventPageFactory, primary_index
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory
from tests.e2e.csp import RECORD_CSP_VIOLATIONS
from tests.e2e.login import verify_with_totp
from tests.e2e.test_manager_shell import _accessible, _login

pytestmark = pytest.mark.django_db(transaction=True)


def _press(page: Page, name: str) -> None:
    """Reach a button with the keyboard focus and press Enter, as a keyboard user does."""
    page.get_by_role("button", name=name).first.focus()
    page.keyboard.press("Enter")


def test_the_vendor_creates_an_event_and_submits_it(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    password = secrets.token_urlsafe(16)
    user = UserFactory.create(email="seller@example.com", password=password)
    device = TOTPDeviceFactory.create(user=user)
    mine = VendorFactory.create(display_name="Shop North", status=Vendor.Status.ACTIVE)
    VendorMemberFactory.create(vendor=mine, user=user)
    primary_index()
    EventPageFactory.create(vendor=VendorFactory.create(display_name="Shop South"), title="Rival")
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    _login(page, live_server.url, "seller@example.com", password)
    verify_with_totp(page, totp_token(device), window=True)
    page.goto(f"{live_server.url}/manager/shop/events/")
    expect(page.get_by_role("heading", name="Events")).to_be_visible()
    expect(page.locator("main")).not_to_contain_text("Rival")
    _accessible(page)
    shot("events-empty")

    page.get_by_role("link", name="New event").focus()
    page.keyboard.press("Enter")
    expect(page.get_by_role("heading", name="New event")).to_be_visible()
    page.get_by_label("Title").fill("Autumn fair")
    page.get_by_label("Summary").fill("Meet the makers at the autumn fair.")
    page.get_by_label("Type").select_option("fair")
    page.get_by_label("Starts at").fill("2027-10-02T09:00")
    page.get_by_label("Ends at").fill("2027-10-02T18:00")
    page.get_by_label("Repeats").select_option("weekly")
    page.get_by_label("Saturday").check()
    page.get_by_label("Attendance").select_option("in_person")
    page.get_by_label("City").fill("Milano")
    page.get_by_label("Country").fill("IT")
    _accessible(page)
    shot("event-form")
    _press(page, "Save draft")

    expect(page.get_by_role("heading", name="Edit the event")).to_be_visible()
    expect(page.locator("main")).to_contain_text("saved as a draft")
    expect(page).to_have_url(re.compile(r"/manager/shop/events/[0-9a-f-]+/$"))
    saved = EventPage.objects.get(title="Autumn fair")
    assert saved.rrule == "FREQ=WEEKLY;BYDAY=SA"
    _accessible(page)
    shot("event-saved")

    _press(page, "Submit for review")
    expect(page).to_have_url(re.compile(r"/manager/shop/events/$"))
    expect(page.locator("main")).to_contain_text("Autumn fair")
    expect(page.locator("main")).to_contain_text("Awaiting review")
    _accessible(page)
    shot("events-submitted")
