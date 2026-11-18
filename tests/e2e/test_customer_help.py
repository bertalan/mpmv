"""E2E (ARC.37): the customer help pages are clean in both themes (axe wcag22aa, no CSP
violation) at 360, 768 and 1280 px, and the footer help link opens the help centre."""

import pytest
from playwright.sync_api import Page, expect
from pytest_django.fixtures import Settings as SettingsWrapper
from pytest_django.live_server_helper import LiveServer

from apps.cms import help_sync
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def _synced(live_server: LiveServer, _cms_site: None) -> None:
    help_sync.sync_help()


@pytest.mark.parametrize("theme", ["base", "ride"])
@pytest.mark.parametrize("width", [360, 768, 1280])
@pytest.mark.parametrize("path", ["/help/", "/help/withdrawal/", "/it/help/shipping/"])
def test_the_help_pages_pass_the_gates(
    theme: str,
    width: int,
    path: str,
    page: Page,
    live_server: LiveServer,
    settings: SettingsWrapper,
) -> None:
    settings.MPMV_THEME_DEFAULT = theme
    settings.MPMV_HELP_URL = ""
    page.emulate_media(reduced_motion="reduce")
    page.set_viewport_size({"width": width, "height": 800})
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    response = page.goto(f"{live_server.url}{path}")
    assert response is not None
    assert response.status == 200
    page.wait_for_load_state("load")
    expect(page.locator("html")).to_have_attribute("data-theme", theme)
    expect(page.locator("main h1")).to_be_visible()
    violations = run_axe(page)
    assert violations == [], format_violations(violations)
    assert csp_violations(page) == [], "\n".join(csp_violations(page))
    link = page.locator("footer .help-link")
    expect(link).to_have_attribute("href", path.split("help")[0] + "help/")
