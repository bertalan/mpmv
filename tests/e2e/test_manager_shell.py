"""E2E (CORE.17): the Manager shell in a real browser.

A member of two vendors signs in on the Manager login, verifies the second factor, picks a
vendor, switches to the other one from the header; platform staff land on the platform space and
get 403 on the shop space. Every Manager page passes axe (WCAG 2.2 AA) with no CSP violation;
screenshots are kept for review.
"""

import re
import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer
from wagtail.models import Collection

from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import totp_token
from apps.catalog.tests.factories import ProductFactory
from apps.core.tests.factories import ImageLicenceFactory
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations
from tests.e2e.login import verify_with_totp
from tests.upload_corpus import png

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
    north = VendorFactory.create(display_name="Shop North")
    VendorMemberFactory.create(vendor=north, user=user)
    ProductFactory.create(vendor=north, status="published")
    ProductFactory.create(vendor=north, status="draft")
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
    expect(page.locator("main")).to_contain_text("Shop North")
    expect(page.locator('[data-kpi="products-published"]')).to_have_text("1")
    expect(page.locator('[data-kpi="products-draft"]')).to_have_text("1")
    _accessible(page)
    shot("shop-dashboard")

    page.get_by_label("Vendor", exact=True).select_option(label="Shop South")
    page.get_by_role("button", name="Switch").click()
    expect(page).to_have_url(re.compile(r"/manager/shop/$"))
    expect(page.locator("main")).to_contain_text("Shop South")
    expect(page.locator("main")).not_to_contain_text("Shop North")
    expect(page.locator('[data-kpi="products-published"]')).to_have_text("0")
    _accessible(page)
    shot("shop-dashboard-switched")

    response = page.goto(f"{live_server.url}/manager/platform/")
    assert response is not None
    assert response.status == 403

    page.goto(f"{live_server.url}/manager/shop/")
    page.get_by_role("button", name="Sign out").click()
    expect(page).to_have_url(re.compile(r"/account/login/"))


def test_platform_staff_land_on_the_platform_space(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    password = secrets.token_urlsafe(16)
    user = UserFactory.create(
        email="ops@example.com", password=password, permissions=["core.platform_owner"]
    )
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
    assert response.status == 403


def test_an_anonymous_visitor_is_sent_to_the_login(page: Page, live_server: LiveServer) -> None:
    page.goto(f"{live_server.url}/manager/platform/")
    expect(page).to_have_url(re.compile(r"/account/login/\?next=/manager/platform/"))


def test_the_owner_edits_the_storefront_profile_and_staff_cannot(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    password = secrets.token_urlsafe(16)
    owner = UserFactory.create(email="owner@example.com", password=password)
    device = TOTPDeviceFactory.create(user=owner)
    shop = VendorFactory.create(display_name="Shop West")
    VendorMemberFactory.create(vendor=shop, user=owner, role="owner")
    # Transactional tests flush the seeded allow list and the root collection: put back what the
    # logo upload uses.
    ImageLicenceFactory.create(code="own-work", name="Own work", source_required=False)
    if Collection.get_first_root_node() is None:
        Collection.add_root(name="Root")
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    _login(page, live_server.url, "owner@example.com", password)
    verify_with_totp(page, totp_token(device))
    page.get_by_role("navigation", name="Manager sections").get_by_role(
        "link", name="Storefront profile"
    ).click()
    expect(page).to_have_url(re.compile(r"/manager/shop/profile/"))
    expect(page.get_by_role("heading", name="Storefront profile")).to_be_visible()
    _accessible(page)
    shot("profile-form")

    # The browser stops typing at the limit; drop it to reach the server-side check.
    page.get_by_label("Tagline").evaluate("el => el.removeAttribute('maxlength')")
    page.get_by_label("Tagline").fill("x" * 201)
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_role("region", name="There is a problem")).to_be_visible()
    expect(page.get_by_label("Tagline")).to_have_attribute("aria-invalid", "true")
    _accessible(page)
    shot("profile-error")

    page.get_by_label("Tagline").fill("Hand made in the west")
    page.get_by_label("Returns and refunds policy").fill("Send it back within 30 days.")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_text("Storefront profile saved.")).to_be_visible()
    expect(page.get_by_label("Tagline")).to_have_value("Hand made in the west")
    _accessible(page)
    shot("profile-saved")

    page.get_by_role("button", name="Save logo").click()
    expect(page.get_by_role("region", name="There is a problem")).to_be_visible()
    _accessible(page)
    shot("logo-error")

    page.get_by_label("Logo image").set_input_files(
        {"name": "logo.png", "mimeType": "image/png", "buffer": png((64, 48))}
    )
    page.get_by_label("Description of the logo").fill("Shop West anvil mark")
    page.get_by_role("button", name="Save logo").click()
    expect(page.get_by_text("Logo saved.")).to_be_visible()
    expect(page.get_by_role("img", name="Shop West anvil mark")).to_be_visible()
    _accessible(page)
    shot("logo-saved")


def test_vendor_staff_have_no_storefront_profile(page: Page, live_server: LiveServer) -> None:
    password = secrets.token_urlsafe(16)
    member = UserFactory.create(email="staff@example.com", password=password)
    device = TOTPDeviceFactory.create(user=member)
    VendorMemberFactory.create(
        vendor=VendorFactory.create(display_name="Shop East"), user=member, role="staff"
    )

    _login(page, live_server.url, "staff@example.com", password)
    verify_with_totp(page, totp_token(device))
    expect(page.get_by_role("link", name="Storefront profile")).to_have_count(0)
    response = page.goto(f"{live_server.url}/manager/shop/profile/")
    assert response is not None
    assert response.status == 404


def test_the_manager_language_switcher_changes_the_language(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    """I18N.12: the switch stays on the unprefixed Manager page, now in the chosen language."""
    password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    user = UserFactory.create(email="switcher@example.com", password=password)
    device = TOTPDeviceFactory.create(user=user)
    VendorMemberFactory.create(vendor=VendorFactory.create(display_name="Shop One"), user=user)
    _login(page, live_server.url, "switcher@example.com", password)
    verify_with_totp(page, totp_token(device))
    expect(page).to_have_url(re.compile(r"/manager/shop/"))
    expect(page.locator("html")).to_have_attribute("lang", "en")
    shot("manager-en")

    page.locator('button[value="it"]').click()
    expect(page).to_have_url(re.compile(r"/manager/shop/"))
    expect(page.locator("html")).to_have_attribute("lang", "it")
    expect(page.locator('button[value="it"]')).to_have_attribute("aria-current", "true")
    shot("manager-it")

    page.locator('button[value="en"]').click()
    expect(page.locator("html")).to_have_attribute("lang", "en")
