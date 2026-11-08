"""E2E (THM.3): the error pages of the `base` theme (spec 14 §14.6, §14.9).

The 404 page renders with `base`: the Tab key alone reaches every control with a visible
focus outline, and no CSP violation is raised.
"""

from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.fixtures import Settings as SettingsWrapper
from pytest_django.live_server_helper import LiveServer

from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations
from tests.e2e.test_base_theme_keyboard import assert_visible_focus, tab_through

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def _help_url(settings: SettingsWrapper) -> None:
    settings.MPMV_HELP_URL = "/about/#help"


def test_the_404_page_renders_with_base_and_is_keyboard_usable(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    response = page.goto(f"{live_server.url}/no-such-page/")
    assert response is not None
    assert response.status == 404
    expect(page.locator("html")).to_have_attribute("data-theme", "base")
    expect(page.locator("main h1")).to_have_count(1)
    expect(page.locator("footer.site-footer .help-link")).to_be_visible()

    stops = tab_through(page)
    assert_visible_focus(stops)
    shot("keyboard")
    assert csp_violations(page) == [], "\n".join(csp_violations(page))
