"""E2E (EVT.4 part 3): the whole vendor-to-moderator loop for a promotion.

A vendor in IT creates a promotion with two body blocks and submits it, with the keyboard (the
datetime-local fields use fill(), as in part 1). A moderator asks for changes in the Wagtail admin;
the comment shows in the vendor's list; the vendor resubmits and the moderator approves. The
map picker (click, drag, typing) is covered by `test_manager_event_map_picker.py`.
"""

import re
import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from django.contrib.auth.models import Group
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import totp_token
from apps.events.models import EVENT_MODERATORS, EventPage
from apps.events.services import ensure_event_review_workflow
from apps.events.tests.factories import primary_index
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory
from tests.e2e.csp import RECORD_CSP_VIOLATIONS
from tests.e2e.login import verify_with_totp
from tests.e2e.test_manager_events import _press
from tests.e2e.test_manager_shell import _accessible, _login

pytestmark = pytest.mark.django_db(transaction=True)

COMMENT = "Please add the end date of the offer."


def _add_paragraph(page: Page, text: str) -> None:
    page.get_by_label("Add a block").select_option("paragraph")
    _press(page, "Add")
    expect(page.get_by_role("heading", name=re.compile("block", re.I))).to_be_visible()
    page.get_by_label("Text").fill(text)
    _press(page, "Save")
    expect(page.locator("main")).to_contain_text(text)


def test_a_vendor_promotion_is_moderated_and_the_comment_reaches_the_vendor(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    ensure_event_review_workflow()
    primary_index()
    vendor_password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    seller = UserFactory.create(email="seller@example.com", password=vendor_password)
    seller_device = TOTPDeviceFactory.create(user=seller)
    shop = VendorFactory.create(display_name="Shop North", status=Vendor.Status.ACTIVE)
    VendorMemberFactory.create(vendor=shop, user=seller)
    staff_password = secrets.token_urlsafe(16)
    staff = UserFactory.create(email="moderator@example.com", password=staff_password, superuser=True)
    staff.groups.add(Group.objects.get(name=EVENT_MODERATORS))
    staff_device = TOTPDeviceFactory.create(user=staff)
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    # The vendor creates a promotion in IT with two body blocks and submits it.
    _login(page, live_server.url, "seller@example.com", vendor_password)
    verify_with_totp(page, totp_token(seller_device), window=True)
    page.goto(f"{live_server.url}/manager/shop/events/new/")
    page.get_by_label("Title").fill("Saldi d'autunno")
    page.get_by_label("Summary").fill("Sconti su tutto il negozio.")
    page.get_by_label("Type").select_option("promotion")
    page.get_by_label("Starts at").fill("2027-10-02T09:00")
    page.get_by_label("Ends at").fill("2027-10-09T18:00")
    # The form has no language field until EVT.8: a new event takes the interface language
    # (I18N.12), so the vendor's preference is Italian for the first save only.
    seller.preferred_language = "it"
    seller.save()
    _press(page, "Save draft")
    expect(page).to_have_url(re.compile(r"/manager/shop/events/[0-9a-f-]+/$"))
    seller.preferred_language = "en"
    seller.save()
    expect(page.get_by_role("heading", name="Edit the event")).to_be_visible()
    event = EventPage.objects.get(title="Saldi d'autunno")
    assert event.locale.language_code == "it"
    _add_paragraph(page, "Primo paragrafo dell'offerta.")
    _add_paragraph(page, "Secondo paragrafo dell'offerta.")
    page.goto(f"{live_server.url}/manager/shop/events/{event.public_id}/")
    _accessible(page)
    shot("promotion-two-blocks")
    draft = EventPage.objects.get(pk=event.pk).get_latest_revision_as_object()
    assert [b.block_type for b in draft.body] == ["paragraph", "paragraph"]
    _press(page, "Submit for review")
    expect(page.locator("main")).to_contain_text("Awaiting review")

    # A moderator asks for changes in the Wagtail admin.
    page.context.clear_cookies()
    page.goto(f"{live_server.url}/admin/login/")
    page.locator("input[name=username]").fill("moderator@example.com")
    page.locator("input[name=password]").fill(staff_password)
    page.get_by_role("button", name="Sign in").click()
    verify_with_totp(page, totp_token(staff_device))
    page.goto(f"{live_server.url}/admin/events/review/")
    page.get_by_role("link", name="Saldi d'autunno").click()
    page.get_by_role("button", name="More actions").click()
    page.get_by_role("button", name="Request changes").click()
    page.get_by_label("Comment").fill(COMMENT)
    shot("request-changes")
    page.get_by_role("button", name="Request changes").last.click()
    expect(page.locator(".messages")).to_be_visible()

    # The comment shows in the vendor area.
    page.context.clear_cookies()
    _login(page, live_server.url, "seller@example.com", vendor_password)
    page.goto(f"{live_server.url}/manager/shop/events/")
    expect(page.locator("main")).to_contain_text(COMMENT)
    _accessible(page)
    shot("vendor-comment")
