"""E2E (CORE.17): the Manager shell in a real browser.

A member of two vendors signs in on the Manager login, verifies the second factor, picks a
vendor, switches to the other one from the header; platform staff land on the platform space and
get 404 on the shop space. Every Manager page passes axe (WCAG 2.2 AA) with no CSP violation;
screenshots are kept for review.
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
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations
from tests.e2e.login import verify_with_totp

pytestmark = pytest.mark.django_db(transaction=True)


def _login(page: Page, base_url: str, email: str, password: str) -> None:
    page.goto(f"{base_url}/account/login/")
    page.get_by_label("Email").fill(email)
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Sign in").click()


def _accessible(page: Page) -> None:
    violations = run_axe(page)
    assert not violations, format_violations(violations)
    assert csp_violations(page) == []


def test_a_member_of_two_vendors_signs_in_and_switches_vendor(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    user = UserFactory.create(email="member@example.com", password=password)
    device = TOTPDeviceFactory.create(user=user)
    VendorMemberFactory.create(vendor=VendorFactory.create(display_name="Shop North"), user=user)
    VendorMemberFactory.create(
        vendor=VendorFactory.create(display_name="Shop South"), user=user, role="staff"
    )
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    _login(page, live_server.url, "member@example.com", password)
    expect(page).to_have_url(re.compile(r"/account/mfa/verify/"))
    verify_with_totp(page, totp_token(device))

    expect(page).to_have_url(re.compile(r"/manager/shop/vendor/"))
    expect(page.get_by_role("heading", name="Choose a vendor")).to_be_visible()
    _accessible(page)
    shot("vendor-picker")

    page.get_by_role("button", name="Shop North").click()
    expect(page).to_have_url(re.compile(r"/manager/shop/$"))
    expect(page.get_by_role("heading", name="Dashboard")).to_be_visible()
    expect(page.get_by_text("Shop North").first).to_be_visible()
    _accessible(page)
    shot("shop-dashboard")

    page.get_by_label("Vendor", exact=True).select_option(label="Shop South")
    page.get_by_role("button", name="Switch").click()
    expect(page).to_have_url(re.compile(r"/manager/shop/$"))
    expect(page.locator("main")).to_contain_text("Shop South")
    expect(page.locator("main")).not_to_contain_text("Shop North")
    _accessible(page)
    shot("shop-dashboard-switched")

    response = page.goto(f"{live_server.url}/manager/platform/")
    assert response is not None
    assert response.status == 404

    page.goto(f"{live_server.url}/manager/shop/")
    page.get_by_role("button", name="Sign out").click()
    expect(page).to_have_url(re.compile(r"/account/login/"))


def test_platform_staff_land_on_the_platform_space(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    password = secrets.token_urlsafe(16)
    user = UserFactory.create(email="ops@example.com", password=password, staff=True)
    device = TOTPDeviceFactory.create(user=user)
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    page.goto(f"{live_server.url}/account/login/")
    _accessible(page)
    shot("login")

    _login(page, live_server.url, "ops@example.com", password)
    verify_with_totp(page, totp_token(device))
    expect(page).to_have_url(re.compile(r"/manager/platform/$"))
    expect(page.get_by_role("heading", name="Dashboard")).to_be_visible()
    expect(page.get_by_role("navigation", name="Manager sections")).to_be_visible()
    _accessible(page)
    shot("platform-dashboard")

    response = page.goto(f"{live_server.url}/manager/shop/")
    assert response is not None
    assert response.status == 404


def test_an_anonymous_visitor_is_sent_to_the_login(page: Page, live_server: LiveServer) -> None:
    page.goto(f"{live_server.url}/manager/platform/")
    expect(page).to_have_url(re.compile(r"/account/login/\?next=/manager/platform/"))
