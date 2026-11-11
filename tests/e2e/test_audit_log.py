"""E2E: staff change a vendor's VAT number and read the change in the audit view (SEC.4)."""

import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import totp_token
from apps.vendors.tests.factories import VendorFactory
from tests.e2e.login import admin_login, verify_with_totp

pytestmark = pytest.mark.django_db(transaction=True)


def test_staff_see_a_vendor_change_with_before_and_after(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    user = UserFactory.create(email="staff@example.com", password=password, superuser=True)
    device = TOTPDeviceFactory.create(user=user)
    vendor = VendorFactory.create(display_name="Vendor A", vat_number="IT00000010025")
    admin_login(page, live_server.url, "staff@example.com", password)
    verify_with_totp(page, totp_token(device), operational=True)

    page.goto(f"{live_server.url}/django-admin/vendors/vendor/{vendor.pk}/change/")
    page.locator("#id_vat_number").fill("IT00000020032")
    page.get_by_role("button", name="Save and continue editing").click()
    expect(page.locator(".messagelist")).to_contain_text("was changed successfully")

    page.get_by_role("link", name="Changes to this vendor").click()
    # The VAT number is DAC7 data: a staff change is a DAC7 correction (VEN.23).
    expect(page.locator("#result_list")).to_contain_text("vendor.dac7_corrected")
    shot("vendor-trail")

    page.locator("#result_list tbody tr").first.locator("a").first.click()
    changes = page.locator("fieldset", has_text="Changes").first
    expect(changes).to_contain_text("vat_number")
    expect(changes).to_contain_text("IT00000010025")
    expect(changes).to_contain_text("IT00000020032")
    shot("entry")
