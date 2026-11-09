"""E2E (CMP.9, WCAG 2.2 SC 3.3.8): accessible authentication in a real browser.

A password and a TOTP code are pasted through the system clipboard (the way a password manager
or an authenticator app hands them over), the show-password toggle works without breaking the
strict CSP, the `autocomplete` tokens are in place, and axe finds no WCAG 2.2 AA violation on
any authentication page: staff login, editors' login, failed login, MFA setup, authenticator
enrolment, recovery codes and verification.
"""

import re
import secrets
from base64 import b32decode
from collections.abc import Callable
from pathlib import Path

import pytest
from django_otp.oath import totp
from playwright.sync_api import Locator, Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import (
    RecoveryCodeDeviceFactory,
    RecoveryCodeFactory,
    TOTPDeviceFactory,
    UserFactory,
)
from apps.accounts.tests.mfa import totp_token
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)


def _paste(page: Page, field: Locator, text: str) -> None:
    """Put `text` on the system clipboard and paste it into `field` with the keyboard."""
    page.evaluate("text => navigator.clipboard.writeText(text)", text)
    field.click()
    page.keyboard.press("ControlOrMeta+KeyV")
    expect(field).to_have_value(text)


def _no_axe_violations(page: Page) -> None:
    violations = run_axe(page)
    assert violations == [], format_violations(violations)


def _logged_in(page: Page) -> None:
    expect(
        page.get_by_role("link", name="Log out").or_(page.get_by_role("button", name="Log out"))
    ).to_be_visible()


@pytest.fixture
def clipboard_page(page: Page, live_server: LiveServer) -> Page:
    page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=live_server.url)
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    return page


def _check_toggle(page: Page, password_field: Locator) -> None:
    toggle = page.get_by_role("button", name="Show password")
    expect(toggle).to_have_attribute("aria-pressed", "false")
    toggle.click()
    expect(password_field).to_have_attribute("type", "text")
    expect(toggle).to_have_attribute("aria-pressed", "true")
    toggle.click()
    expect(password_field).to_have_attribute("type", "password")
    expect(toggle).to_have_attribute("aria-pressed", "false")


def test_staff_paste_a_password_and_a_totp_code(
    clipboard_page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page = clipboard_page
    password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    user = UserFactory.create(email="staff@example.com", password=password, superuser=True)
    device = TOTPDeviceFactory.create(user=user)
    RecoveryCodeFactory.create(device=RecoveryCodeDeviceFactory.create(user=user))

    page.goto(f"{live_server.url}/django-admin/login/")
    email = page.get_by_label("Email")
    secret_field = page.get_by_label("Password")
    expect(email).to_have_attribute("autocomplete", "username")
    expect(secret_field).to_have_attribute("autocomplete", "current-password")
    _no_axe_violations(page)

    email.fill("staff@example.com")
    _paste(page, secret_field, password)
    _check_toggle(page, secret_field)
    shot("login-pasted")
    page.get_by_role("button", name="Log in").click()

    expect(page).to_have_url(re.compile(r"/account/mfa/verify/"))
    code = page.get_by_label("Authentication code")
    expect(code).to_have_attribute("autocomplete", "one-time-code")
    expect(code).to_have_attribute("inputmode", "numeric")
    expect(page.get_by_label("Recovery code")).to_have_attribute("autocomplete", "one-time-code")
    _no_axe_violations(page)
    _paste(page, code, totp_token(device))
    shot("verify-pasted")
    page.get_by_role("button", name="Verify", exact=True).click()
    _logged_in(page)
    assert csp_violations(page) == []


def test_a_failed_login_explains_itself_accessibly(
    clipboard_page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page = clipboard_page
    password = secrets.token_urlsafe(16)
    UserFactory.create(email="staff@example.com", password=password, superuser=True)

    page.goto(f"{live_server.url}/django-admin/login/")
    page.get_by_label("Email").fill("staff@example.com")
    _paste(page, page.get_by_label("Password"), "x" + password)
    page.get_by_role("button", name="Log in").click()

    secret_field = page.get_by_label("Password")
    expect(secret_field).to_have_attribute("aria-invalid", "true")
    error_id = secret_field.get_attribute("aria-describedby")
    assert error_id
    expect(page.locator(f"#{error_id}")).to_contain_text("correct email and password")
    _no_axe_violations(page)
    shot("login-error")
    assert csp_violations(page) == []


def test_editors_paste_their_password_on_the_wagtail_login(
    clipboard_page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page = clipboard_page
    password = secrets.token_urlsafe(16)
    user = UserFactory.create(email="editor@example.com", password=password, superuser=True)
    device = TOTPDeviceFactory.create(user=user)

    page.goto(f"{live_server.url}/admin/login/")
    username = page.locator("input[name=username]")
    secret_field = page.locator("input[name=password]")
    expect(username).to_have_attribute("autocomplete", "username")
    expect(secret_field).to_have_attribute("autocomplete", "current-password")
    expect(page.locator("form.login-form")).not_to_have_attribute("autocomplete", "off")
    _no_axe_violations(page)

    username.fill("editor@example.com")
    _paste(page, secret_field, password)
    _check_toggle(page, secret_field)
    shot("wagtail-login-pasted")
    page.get_by_role("button", name="Sign in").click()

    expect(page).to_have_url(re.compile(r"/account/mfa/verify/"))
    _paste(page, page.get_by_label("Authentication code"), totp_token(device))
    page.get_by_role("button", name="Verify", exact=True).click()
    expect(page).to_have_url(re.compile(r"/admin/$"))
    assert csp_violations(page) == []


def test_mfa_enrolment_pages_pass_axe_and_accept_a_pasted_code(
    clipboard_page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page = clipboard_page
    password = secrets.token_urlsafe(16)
    UserFactory.create(email="staff@example.com", password=password, staff=True)

    page.goto(f"{live_server.url}/django-admin/login/")
    page.get_by_label("Email").fill("staff@example.com")
    _paste(page, page.get_by_label("Password"), password)
    page.get_by_role("button", name="Log in").click()

    expect(page).to_have_url(re.compile(r"/account/mfa/setup/"))
    _no_axe_violations(page)
    page.get_by_role("link", name="Use an authenticator app").click()
    _no_axe_violations(page)
    secret = page.locator("#totp-secret").inner_text().replace(" ", "")

    code = page.get_by_label("Authentication code")
    expect(code).to_have_attribute("autocomplete", "one-time-code")
    _paste(page, code, "000000")
    page.get_by_role("button", name="Confirm").click()
    code = page.get_by_label("Authentication code")
    expect(code).to_have_attribute("aria-invalid", "true")
    _no_axe_violations(page)
    shot("enrolment-error")

    _paste(page, code, str(totp(b32decode(secret))).zfill(6))
    page.get_by_role("button", name="Confirm").click()
    expect(page.locator("ol.recovery-codes li")).to_have_count(10)
    _no_axe_violations(page)
    shot("recovery-codes")
    assert csp_violations(page) == []
