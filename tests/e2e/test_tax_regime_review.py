"""E2E: staff review a vendor's tax regime change request (TAX.2): the pending notice, the
request with the vendor's history, and the approval that adds the new VAT regime version."""

import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import totp_token
from apps.core.tests.factories import utc
from apps.tax.models import TaxRegimeChangeRequest, VendorTaxProfile
from apps.tax.tests.factories import TaxRegimeChangeRequestFactory, VendorTaxProfileFactory
from tests.e2e.login import admin_login, verify_with_totp

pytestmark = pytest.mark.django_db(transaction=True)


def test_staff_approve_a_tax_regime_change(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    current = VendorTaxProfileFactory.create(vendor__fiscal_regime="RF01", valid_from=utc(2026))
    request = TaxRegimeChangeRequestFactory.create(vendor=current.vendor)
    password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    staff = UserFactory.create(email="staff@example.com", password=password, superuser=True)
    device = TOTPDeviceFactory.create(user=staff)
    admin_login(page, live_server.url, "staff@example.com", password)
    verify_with_totp(page, totp_token(device), operational=True)

    page.goto(f"{live_server.url}/django-admin/tax/taxregimechangerequest/")
    expect(page.locator(".messagelist")).to_contain_text("waiting for review")
    page.get_by_role("link", name=str(request.vendor)).click()
    expect(page.locator(".field-profile_history")).to_contain_text("Domestic only")
    shot("request")

    page.get_by_role("link", name="Approve").click()
    page.get_by_label("Reason").fill("OSS registration checked.")
    shot("approve")
    page.get_by_role("button", name="Approve").click()
    expect(page.locator(".messagelist")).to_contain_text("approved")
    expect(page.locator(".field-profile_history")).to_contain_text("OSS")
    shot("approved")

    request.refresh_from_db()
    assert request.status == TaxRegimeChangeRequest.Status.APPROVED
    assert VendorTaxProfile.objects.get(valid_to__isnull=True).regime == "oss"
