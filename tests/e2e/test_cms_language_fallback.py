"""E2E (I18N.21): a CMS page shown in the primary language in place of a missing translation
carries a polite notice that passes axe (WCAG 2.2 AA, SC 3.1.2) in the base and the ride theme,
leaves the keyboard free (no focus trap, the notice itself is not a tab stop) and keeps the CSP
intact."""

from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.fixtures import Settings as SettingsWrapper
from pytest_django.live_server_helper import LiveServer
from wagtail.models import Site

from apps.cms.services import seed_site
from apps.cms.tests.factories import StandardPageFactory
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations
from tests.i18n import language_prefix, other_language

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("theme", ["base", "ride"])
def test_the_fallback_notice_passes_axe_and_leaves_the_keyboard_free(
    page: Page,
    live_server: LiveServer,
    settings: SettingsWrapper,
    shot: Callable[[str], Path],
    theme: str,
) -> None:
    settings.MPMV_THEME_DEFAULT = theme
    seed_site()
    home = Site.objects.get(is_default_site=True).root_page
    StandardPageFactory.create(title="Only in the primary", slug="primary-only", parent=home)
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    page.goto(f"{live_server.url}{language_prefix(other_language())}/primary-only/")
    notice = page.locator(".alert--info[role=status]")
    expect(notice).to_be_visible()
    violations = run_axe(page)
    assert not violations, format_violations(violations)
    assert csp_violations(page) == []
    shot(f"fallback-notice-{theme}")

    seen = set()
    for _ in range(6):
        page.keyboard.press("Tab")
        seen.add(page.evaluate("document.activeElement.outerHTML.slice(0, 80)"))
        assert not notice.evaluate("(el) => el.contains(document.activeElement)")
    assert len(seen) > 1
