"""E2E: the staff admin keeps working under the strict Content-Security-Policy (SEC.1)."""

import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import UserFactory

pytestmark = pytest.mark.django_db(transaction=True)

# Runs before any page script: records every CSP violation the browser reports.
RECORD_CSP_VIOLATIONS = """
window.__cspViolations = [];
document.addEventListener("securitypolicyviolation", (event) => {
  window.__cspViolations.push(`${event.effectiveDirective} blocked ${event.blockedURI}`);
});
"""


def csp_violations(page: Page) -> list[str]:
    violations: list[str] = page.evaluate("window.__cspViolations")
    return violations


def test_admin_works_under_strict_csp(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    UserFactory.create(email="staff@example.com", password=password, superuser=True)
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    response = page.goto(f"{live_server.url}/django-admin/login/")
    assert response is not None
    policy = response.headers["content-security-policy"]
    assert "'unsafe-inline'" not in policy
    expect(page.get_by_label("Email")).to_be_visible()
    assert csp_violations(page) == []
    shot("login")

    page.get_by_label("Email").fill("staff@example.com")
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Log in").click()
    expect(
        page.get_by_role("link", name="Log out").or_(page.get_by_role("button", name="Log out"))
    ).to_be_visible()
    assert csp_violations(page) == []
    shot("index")

    # Pages that load more admin JavaScript: changelist (actions) and change form (widgets).
    page.goto(f"{live_server.url}/django-admin/accounts/user/")
    expect(page.get_by_role("link", name="staff@example.com")).to_be_visible()
    assert csp_violations(page) == []
    shot("changelist")

    page.get_by_role("link", name="staff@example.com").click()
    expect(page.get_by_label("Full name")).to_be_visible()
    assert csp_violations(page) == []
    shot("change-form")
