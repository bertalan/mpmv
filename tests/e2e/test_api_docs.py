"""E2E: the interactive API v1 docs render under the strict Content-Security-Policy (CORE.7)."""

from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

pytestmark = pytest.mark.django_db(transaction=True)

# Runs before any page script: records every CSP violation the browser reports.
RECORD_CSP_VIOLATIONS = """
window.__cspViolations = [];
document.addEventListener("securitypolicyviolation", (event) => {
  window.__cspViolations.push(`${event.effectiveDirective} blocked ${event.blockedURI}`);
});
"""


def test_api_docs_render_under_strict_csp(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    response = page.goto(f"{live_server.url}/api/v1/docs")
    assert response is not None
    assert "'unsafe-inline'" not in response.headers["content-security-policy"]

    expect(page.get_by_text("/api/v1/me")).to_be_visible()
    expect(page.get_by_role("button", name="Authorize")).to_be_visible()
    violations: list[str] = page.evaluate("window.__cspViolations")
    assert violations == []
    shot("docs")
