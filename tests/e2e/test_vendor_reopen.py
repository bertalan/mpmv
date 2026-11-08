"""E2E: staff reopen a closed vendor from its page with a reason (VEN.21); the reopened vendor is
onboarding and its checklist asks for the contract in force again."""

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
from apps.vendors.tests.factories import ContractAcceptanceFactory, VendorContractFactory
from tests.e2e.login import admin_login, verify_with_totp

pytestmark = pytest.mark.django_db(transaction=True)


def test_staff_reopen_a_closed_vendor_with_a_reason(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    contract = VendorContractFactory.create()
    acceptance = ContractAcceptanceFactory.create(  # accepted before the closure
        contract=contract, vendor__status=Vendor.Status.CLOSED
    )
    vendor = acceptance.vendor
    password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    staff = UserFactory.create(email="staff@example.com", password=password, superuser=True)
    device = TOTPDeviceFactory.create(user=staff)
    admin_login(page, live_server.url, "staff@example.com", password)
    verify_with_totp(page, totp_token(device))

    page.goto(f"{live_server.url}/django-admin/vendors/vendor/{vendor.pk}/change/")
    page.get_by_role("link", name="Reopen this vendor").click()
    page.get_by_role("button", name="Reopen").click()  # no reason: refused
    expect(page.locator(".errorlist")).to_be_visible()
    shot("reason-required")

    page.get_by_label("Reason").fill("The owner asked to sell again after the winter break.")
    page.get_by_role("button", name="Reopen").click()
    expect(page.locator(".messagelist")).to_contain_text("accept the contract in force again")
    checklist = page.locator(".field-onboarding")
    expect(checklist).to_contain_text(f"Contract version {contract.version}: not accepted")
    expect(checklist).to_contain_text("Not ready to activate")
    shot("reopened")

    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.ONBOARDING
    entry = AuditLog.objects.get(action="vendor.reopened")
    assert entry.after is not None
    assert entry.after["reason"].startswith("The owner asked")
