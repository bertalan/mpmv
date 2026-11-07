"""E2E (CORE.4): a staff member is forced to enrol an authenticator app on first login, sees
the recovery codes once, and later signs in with a recovery code."""

import re
import secrets
from base64 import b32decode
from collections.abc import Callable
from pathlib import Path

import pytest
from django_otp.oath import totp
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import totp_token
from apps.core.models import AuditLog
from tests.e2e.login import admin_login, verify_with_totp

pytestmark = pytest.mark.django_db(transaction=True)

RECORD_CSP_VIOLATIONS = """
window.__cspViolations = [];
document.addEventListener("securitypolicyviolation", (event) => {
  window.__cspViolations.push(`${event.effectiveDirective} blocked ${event.blockedURI}`);
});
"""


def test_first_login_forces_totp_enrolment_then_a_recovery_code_signs_in(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    UserFactory.create(email="staff@example.com", password=password, staff=True)
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    admin_login(page, live_server.url, "staff@example.com", password)
    expect(page).to_have_url(re.compile(r"/account/mfa/setup/"))
    expect(page.get_by_role("heading", name="Set up two-factor authentication")).to_be_visible()
    shot("forced-setup")

    page.get_by_role("link", name="Use an authenticator app").click()
    expect(page.locator("svg").first).to_be_visible()  # the QR code
    secret = page.locator("#totp-secret").inner_text().replace(" ", "")
    shot("totp-qr")

    token = str(totp(b32decode(secret))).zfill(6)
    page.get_by_label("Authentication code").fill(token)
    page.get_by_role("button", name="Confirm").click()
    codes = page.locator("ol.recovery-codes li").all_inner_texts()
    assert len(codes) == 10
    shot("recovery-codes")

    page.get_by_role("link", name="Continue").click()
    expect(
        page.get_by_role("link", name="Log out").or_(page.get_by_role("button", name="Log out"))
    ).to_be_visible()
    shot("admin")
    assert page.evaluate("window.__cspViolations") == []
    assert AuditLog.objects.filter(action="accounts.mfa.enrolled").count() == 1

    page.context.clear_cookies()
    admin_login(page, live_server.url, "staff@example.com", password)
    expect(page).to_have_url(re.compile(r"/account/mfa/verify/"))
    shot("verify")
    page.get_by_label("Recovery code").fill(codes[0])
    page.get_by_role("button", name="Use recovery code").click()
    expect(
        page.get_by_role("link", name="Log out").or_(page.get_by_role("button", name="Log out"))
    ).to_be_visible()
    shot("signed-in-with-recovery-code")
    assert AuditLog.objects.filter(action="accounts.mfa.recovery_code_used").count() == 1


def test_a_wrong_code_keeps_the_admin_closed(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    password = secrets.token_urlsafe(16)
    user = UserFactory.create(email="staff@example.com", password=password, staff=True)
    device = TOTPDeviceFactory.create(user=user)

    admin_login(page, live_server.url, "staff@example.com", password)
    verify_with_totp(page, totp_token(device, offset_steps=10))
    expect(page.get_by_text("Invalid code")).to_be_visible()
    shot("invalid-code")

    page.goto(f"{live_server.url}/django-admin/")
    expect(page).to_have_url(re.compile(r"/account/mfa/verify/"))
