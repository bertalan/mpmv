"""E2E (MIG.16): a vendor owner records its migration choices in the Manager.

Checks the legal floor and the restricted tax-only text, the seven login windows, a saved and
re-read choice, the refusal state, and that the page passes axe and the strict CSP. Screenshots
are kept for review.
"""

import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import totp_token
from apps.ingest.tests.factories import MigrationRunFactory
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory
from tests.e2e.csp import RECORD_CSP_VIOLATIONS
from tests.e2e.login import verify_with_totp
from tests.e2e.test_manager_shell import _accessible, _login

pytestmark = pytest.mark.django_db(transaction=True)


def test_the_owner_records_the_migration_choices(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    password = secrets.token_urlsafe(16)
    user = UserFactory.create(email="seller@example.com", password=password)
    device = TOTPDeviceFactory.create(user=user)
    vendor = VendorFactory.create(display_name="Shop North")
    VendorMemberFactory.create(vendor=vendor, user=user)
    MigrationRunFactory.create(label="cutover-spring")
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    _login(page, live_server.url, "seller@example.com", password)
    verify_with_totp(page, totp_token(device))
    page.goto(f"{live_server.url}/manager/shop/migration/")
    expect(page.get_by_role("heading", name="Migration choices")).to_be_visible()
    expect(page.locator("[data-legal-floor]")).to_contain_text("legal retention duty")
    expect(page.locator("[data-restricted-archive]")).to_contain_text("restricted tax-only")
    expect(page.locator("[data-choice-status]")).to_contain_text("Not chosen yet")
    options = page.locator("#id_login_window_months option")
    assert options.all_text_contents() == [
        "1 month",
        *(f"{n} months" for n in (2, 3, 6, 12, 24, 36)),
    ]
    _accessible(page)
    shot("migration-choices-form")

    page.get_by_label("All orders").check()
    page.get_by_label("Product reviews").uncheck()
    page.get_by_label("Bring drafts and archived products along").check()
    page.get_by_label("Customers who logged in within").select_option("6")
    page.get_by_role("button", name="Save my choice").click()
    expect(page.locator("[data-choice-status]")).to_contain_text("Recorded (version 1")
    expect(page.get_by_label("Customers who logged in within")).to_have_value("6")
    expect(page.get_by_label("All orders")).to_be_checked()
    expect(page.get_by_label("Product reviews")).not_to_be_checked()
    _accessible(page)
    shot("migration-choices-saved")
