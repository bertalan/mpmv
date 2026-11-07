"""E2E: staff create a vendor with a translation and move it to onboarding (VEN.1)."""

import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import UserFactory
from apps.core.models import AuditLog
from apps.vendors.models import Vendor

pytestmark = pytest.mark.django_db(transaction=True)


def test_staff_create_vendor_and_start_onboarding(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    UserFactory.create(email="staff@example.com", password=password, superuser=True)
    page.goto(f"{live_server.url}/django-admin/login/")
    page.get_by_label("Email").fill("staff@example.com")
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Log in").click()

    page.goto(f"{live_server.url}/django-admin/vendors/vendor/add/")
    page.get_by_label("Legal name").fill("Vendor A S.r.l.")
    page.get_by_label("Display name").fill("Vendor A")
    page.get_by_label("Country").fill("IT")
    page.get_by_label("Contact email").fill("vendor-a@example.com")
    page.locator("#id_translations-0-language").select_option("en")
    page.locator("#id_translations-0-tagline").fill("Outdoor gear")
    shot("filled")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.locator(".messagelist")).to_contain_text("was added successfully")

    vendor = Vendor.objects.get()
    assert vendor.slug == "vendor-a"
    assert vendor.translations.get(language="en").tagline == "Outdoor gear"

    page.goto(f"{live_server.url}/django-admin/vendors/vendor/")
    page.locator("input[name='_selected_action']").first.check()
    page.locator("select[name='action']").select_option("move_to_onboarding")
    page.get_by_role("button", name="Run").click()
    expect(page.locator(".messagelist")).to_contain_text("onboarding")
    shot("onboarding")

    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.ONBOARDING
    assert AuditLog.objects.filter(action="vendor.status_changed").count() == 1
