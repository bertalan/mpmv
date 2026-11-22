"""E2E (CMS.16): a page with an accordion works with scripts disabled, each section opens from the
keyboard (Enter and Space on the summary), and the page is axe `wcag22aa` and CSP clean under both
themes at 360, 768 and 1280 px."""

import pytest
from playwright.sync_api import Browser, Page, expect
from pytest_django.fixtures import Settings as SettingsWrapper
from pytest_django.live_server_helper import LiveServer

from apps.cms.models import HomePage
from apps.core.i18n import primary_language
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)

SECTIONS = [
    {"title": "Shipping", "body": "<p>Two days.</p>"},
    {"title": "Returns", "body": "<p>Thirty days.</p>"},
]


@pytest.fixture
def _accordion_home(live_server: LiveServer, _cms_site: None) -> None:
    home = HomePage.objects.get(locale__language_code=primary_language())
    home.body = [("accordion", {"sections": SECTIONS, "open_first": False})]
    home.save_revision().publish()


@pytest.mark.usefixtures("_accordion_home")
@pytest.mark.parametrize("theme", ["base", "ride"])
@pytest.mark.parametrize("width", [360, 768, 1280])
def test_the_accordion_is_accessible_and_csp_clean(
    theme: str, width: int, page: Page, live_server: LiveServer, settings: SettingsWrapper
) -> None:
    settings.MPMV_THEME_DEFAULT = theme
    page.set_viewport_size({"width": width, "height": 900})
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    page.goto(f"{live_server.url}/")
    page.wait_for_load_state("load")
    expect(page.locator(".block-accordion summary")).to_have_count(2)
    page.locator(".block-accordion summary").first.click()
    expect(page.get_by_text("Two days.")).to_be_visible()
    violations = run_axe(page)
    assert violations == [], format_violations(violations)
    assert csp_violations(page) == [], "\n".join(csp_violations(page))


@pytest.mark.usefixtures("_accordion_home")
@pytest.mark.parametrize("theme", ["base", "ride"])
@pytest.mark.parametrize("key", ["Enter", "Space"])
def test_a_section_opens_and_closes_from_the_keyboard_without_scripts(
    theme: str,
    key: str,
    browser: Browser,
    live_server: LiveServer,
    settings: SettingsWrapper,
) -> None:
    settings.MPMV_THEME_DEFAULT = theme
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        page.goto(f"{live_server.url}/")
        details = page.locator(".block-accordion details").first
        expect(details).not_to_have_attribute("open", "")
        page.locator(".block-accordion summary").first.focus()
        page.keyboard.press(key)
        expect(details).to_have_attribute("open", "")
        expect(page.get_by_text("Two days.")).to_be_visible()
        page.keyboard.press(key)
        expect(details).not_to_have_attribute("open", "")
    finally:
        context.close()
