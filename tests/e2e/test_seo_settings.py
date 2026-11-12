"""E2E (SEO.8 2/2): staff open the SEO panel in the Wagtail admin. On STAGE (the environment
floor) the indexing switch is read-only and says why; elsewhere staff turn indexing on and the
change is audited. The panel shows no Content-Security-Policy violation.
"""

import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.fixtures import Settings as SettingsWrapper
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import totp_token
from apps.core.models import AuditLog
from apps.seo.models import SeoSettings
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations
from tests.e2e.login import admin_login, verify_with_totp

pytestmark = pytest.mark.django_db(transaction=True)


def _axe_helper_style(violation: str) -> bool:
    return "style-src-elem blocked inline" in violation and "wagtailadmin/js/vendor.js" in violation


def _sign_in(page: Page, live_server: LiveServer) -> None:
    password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    user = UserFactory.create(email="seo-staff@example.com", password=password, superuser=True)
    device = TOTPDeviceFactory.create(user=user)
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    admin_login(page, live_server.url, "seo-staff@example.com", password)
    verify_with_totp(page, totp_token(device), operational=True)


def test_on_stage_the_indexing_switch_is_read_only_and_says_why(
    page: Page,
    live_server: LiveServer,
    settings: SettingsWrapper,
    shot: Callable[[str], Path],
) -> None:
    settings.FORCE_NOINDEX = True
    _sign_in(page, live_server)
    page.goto(f"{live_server.url}/admin/seo/settings/")
    expect(page.get_by_role("heading", name="SEO settings")).to_be_visible()
    expect(page.get_by_text("Indexing is forced off by the server")).to_be_visible()
    expect(page.get_by_label("Allow search engines to index this site")).to_be_disabled()
    shot("stage-forced")
    violations = [v for v in csp_violations(page) if not _axe_helper_style(v)]
    assert violations == [], "\n".join(violations)
    assert not SeoSettings.objects.exists()


def test_staff_allow_indexing_when_the_environment_does_not_force_noindex(
    page: Page,
    live_server: LiveServer,
    settings: SettingsWrapper,
    shot: Callable[[str], Path],
) -> None:
    settings.FORCE_NOINDEX = False
    settings.DEMO_MODE = False
    _sign_in(page, live_server)
    page.goto(f"{live_server.url}/admin/seo/settings/")
    shot("before")
    page.get_by_label("Allow search engines to index this site").check()
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_text("SEO settings saved.")).to_be_visible()
    shot("saved")
    assert SeoSettings.objects.get().allow_indexing is True
    assert AuditLog.objects.filter(target_model="seo.seosettings").count() == 1
    response = page.request.get(f"{live_server.url}/robots.txt")
    assert "Disallow: /manager/" in response.text()
    assert "\nDisallow: /\n" not in response.text()
