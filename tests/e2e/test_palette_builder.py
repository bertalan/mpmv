"""E2E (THM.34 2/2): the palette builder in the Wagtail admin, in a real browser.

A keyboard-only session picks a failing primary, sees the pair and its ratio, uses the nearest
compliant shade and saves; while a pair fails the save button is disabled and the reason is in
text; with JavaScript off the same failing palette is refused on submit; no CSP violation; axe
(WCAG 2.2 AA) is clean; baselines at 375 and 1280 px.
"""

import re

import pytest
from django.test import Client
from django.urls import reverse
from playwright._impl._api_structures import SetCookieParam
from playwright.sync_api import Browser, Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.themes.models import BrandPalette
from apps.themes.palettes import PRESETS
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.palette import _defaults
from tests.e2e.test_admin_csp import RECORD_CSP_VIOLATIONS, csp_violations
from tests.e2e.visual import assert_matches_baseline, require_visual_mode

pytestmark = pytest.mark.django_db(transaction=True)

FAILING = "#FFE600"


def session_cookie(live_server: LiveServer) -> SetCookieParam:
    client = Client()
    verified_login(client, UserFactory.create(superuser=True))
    value = client.cookies["sessionid"].value
    return {"name": "sessionid", "value": value, "url": live_server.url}


def seed_presets() -> None:
    """A transactional e2e test starts with the data migration's rows flushed."""
    for slug in PRESETS:
        BrandPalette.objects.update_or_create(slug=slug, defaults=_defaults(slug))


def open_builder(page: Page, live_server: LiveServer) -> None:
    seed_presets()
    page.context.add_cookies([session_cookie(live_server)])
    page.goto(f"{live_server.url}{reverse('themes_admin:palette_builder')}")
    expect(page.locator("#palette-builder")).to_be_visible()
    expect(page.locator("#palette-preview")).to_be_visible()  # the script has run


def type_colour(page: Page, field: str, value: str) -> None:
    page.locator(field).focus()
    page.keyboard.press("ControlOrMeta+A")
    page.keyboard.type(value)


def test_keyboard_only_failing_primary_use_this_and_save(
    page: Page, live_server: LiveServer
) -> None:
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    open_builder(page, live_server)
    summary = page.locator("#palette-failing")
    save = page.locator('button[value="save"]')
    expect(summary).to_contain_text("Every pair passes")
    expect(save).to_be_enabled()

    type_colour(page, "#id_primary", FAILING)
    expect(summary).to_contain_text(re.compile(r"\d+ pairs? fails?"))
    expect(save).to_be_disabled()
    expect(page.locator("#palette-save-reason")).to_contain_text("Save is off while a pair fails")
    failing_row = page.locator('tr[data-pair-result="fail"]').first
    expect(failing_row).to_contain_text(re.compile(r"\d+\.\d\d:1"))
    expect(page.locator("#palette-preview-css")).to_have_attribute(
        "href", re.compile("primary=%23FFE600")
    )

    use = page.locator("#palette-pairs button[data-field]").first
    use.focus()
    page.keyboard.press("Enter")
    expect(save).to_be_enabled()
    expect(summary).to_contain_text("Every pair passes")
    assert page.locator("#id_primary").input_value() != FAILING

    page.locator("#id_name").focus()
    page.keyboard.type("Keyboard palette")
    save.focus()
    page.keyboard.press("Enter")
    page.wait_for_url(re.compile("start=keyboard-palette"))
    assert BrandPalette.objects.filter(slug="keyboard-palette").exists()
    assert csp_violations(page) == []


def test_preview_uses_the_candidate_colours_through_the_generated_stylesheet(
    page: Page, live_server: LiveServer
) -> None:
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    open_builder(page, live_server)
    type_colour(page, "#id_primary", "#0B5C7A")
    expect(page.locator("#palette-preview-css")).to_have_attribute(
        "href", re.compile("primary=%230B5C7A")
    )
    button = page.locator(".pp-button")
    expect(button).to_have_css("background-color", "rgb(11, 92, 122)")
    assert csp_violations(page) == []


def test_builder_has_no_axe_violation_passing_and_failing(
    page: Page, live_server: LiveServer
) -> None:
    open_builder(page, live_server)
    violations = run_axe(page)
    assert violations == [], format_violations(violations)
    type_colour(page, "#id_primary", FAILING)
    expect(page.locator("#palette-failing")).to_contain_text("fail")
    # The preview shows the failing candidate on purpose; the form and the pair list must pass.
    form_parts = "#palette-pairs, #palette-failing, #palette-save-reason, .palette-picker"
    violations = run_axe(page, include=form_parts)
    assert violations == [], format_violations(violations)


def test_without_javascript_the_failing_palette_is_refused_on_submit(
    browser: Browser, live_server: LiveServer
) -> None:
    context = browser.new_context(java_script_enabled=False)
    try:
        seed_presets()
        context.add_cookies([session_cookie(live_server)])
        page = context.new_page()
        page.goto(f"{live_server.url}{reverse('themes_admin:palette_builder')}")
        page.locator("#id_primary").fill(FAILING)
        page.locator("#id_name").fill("No script")
        page.locator('button[value="check"]').click()
        expect(page.locator("#palette-save-reason")).to_contain_text("Save is off")
        expect(page.locator('button[value="save"]')).to_be_disabled()
        assert not BrandPalette.objects.filter(slug="no-script").exists()
    finally:
        context.close()


@pytest.mark.parametrize("width", [375, 1280])
def test_builder_matches_its_visual_baseline(
    page: Page, live_server: LiveServer, width: int
) -> None:
    require_visual_mode()
    page.emulate_media(reduced_motion="reduce")
    page.set_viewport_size({"width": width, "height": 900})
    open_builder(page, live_server)
    type_colour(page, "#id_primary", FAILING)
    expect(page.locator("#palette-failing")).to_contain_text("fail")
    assert_matches_baseline(page, theme="admin", name="palette-builder", width=width)
