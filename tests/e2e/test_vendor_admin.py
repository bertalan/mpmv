"""E2E: staff create a vendor with a translation and move it to onboarding (VEN.1), complete
its legal identity while the form shows what a payout still needs (VEN.12), and activate it once
its owner accepted the contract (VEN.5)."""

import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import totp_token
from apps.core.models import AuditLog
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import (
    ContractAcceptanceFactory,
    VendorContractFactory,
    VendorFactory,
)
from apps.vendors.tests.fakes import seed_legal_requirements
from tests.e2e.login import verify_with_totp

pytestmark = pytest.mark.django_db(transaction=True)


def staff_login(page: Page, live_server: LiveServer) -> None:
    password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    user = UserFactory.create(email="staff@example.com", password=password, superuser=True)
    device = TOTPDeviceFactory.create(user=user)  # staff must verify a second factor (CORE.4)
    page.goto(f"{live_server.url}/django-admin/login/")
    page.get_by_label("Email").fill("staff@example.com")
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Log in").click()
    verify_with_totp(page, totp_token(device))


def test_staff_create_vendor_and_start_onboarding(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    staff_login(page, live_server)

    page.goto(f"{live_server.url}/django-admin/vendors/vendor/add/")
    page.get_by_label("Legal name").fill("Vendor A S.r.l.")
    page.get_by_label("Display name").fill("Vendor A")
    page.locator("#id_country").fill("IT")  # other "country" labels exist
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


def test_staff_complete_an_incomplete_legal_profile(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    seed_legal_requirements()  # transactional tests flush the seeded rows
    vendor = VendorFactory.create(italian_company=True, iban="", pec="", fiscal_regime="")
    staff_login(page, live_server)

    page.goto(f"{live_server.url}/django-admin/vendors/vendor/{vendor.pk}/change/")
    readiness = page.locator(".field-missing_for_payout")
    expect(readiness).to_contain_text("Missing: fiscal regime, PEC address, IBAN.")
    shot("incomplete")

    # Saving a partial profile is allowed: only the fiscal regime is added here.
    page.locator("#id_fiscal_regime").select_option("RF01")
    page.get_by_role("button", name="Save and continue editing").click()
    expect(page.locator(".messagelist")).to_contain_text("was changed successfully")
    expect(readiness).to_contain_text("Missing: PEC address, IBAN.")

    page.locator("#id_pec").fill("vendor-a@pec.example.com")
    page.locator("#id_iban").fill("IT60 X054 2811 1010 0000 0123 456")
    page.get_by_role("button", name="Save and continue editing").click()
    expect(readiness).to_contain_text("the legal profile is complete for payouts")
    shot("complete")

    vendor.refresh_from_db()
    assert vendor.iban == "IT60X0542811101000000123456"
    assert AuditLog.objects.filter(action="vendor.updated").count() == 2


def test_staff_activate_a_vendor_once_the_contract_is_accepted(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    """VEN.5: the checklist on the vendor page; activation waits for the contract only."""
    contract = VendorContractFactory.create()
    vendor = VendorFactory.create(status=Vendor.Status.ONBOARDING)
    staff_login(page, live_server)

    page.goto(f"{live_server.url}/django-admin/vendors/vendor/{vendor.pk}/change/")
    checklist = page.locator(".field-onboarding")
    expect(checklist).to_contain_text(f"Contract version {contract.version}: not accepted")
    expect(checklist).to_contain_text("Not ready to activate")
    shot("checklist")

    page.goto(f"{live_server.url}/django-admin/vendors/vendor/")
    page.locator("input[name='_selected_action']").first.check()
    page.locator("select[name='action']").select_option("move_to_active")
    page.get_by_role("button", name="Run").click()
    expect(page.locator(".messagelist")).to_contain_text("cannot be activated")
    shot("refused")

    ContractAcceptanceFactory.create(vendor=vendor, contract=contract)  # the owner accepted
    page.goto(f"{live_server.url}/django-admin/vendors/vendor/{vendor.pk}/change/")
    expect(page.locator(".field-onboarding")).to_contain_text("Ready to activate")

    page.goto(f"{live_server.url}/django-admin/vendors/vendor/")
    page.locator("input[name='_selected_action']").first.check()
    page.locator("select[name='action']").select_option("move_to_active")
    page.get_by_role("button", name="Run").click()
    expect(page.locator(".messagelist")).to_contain_text("moved to active")
    shot("active")

    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.ACTIVE
