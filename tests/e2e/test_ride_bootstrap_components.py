"""E2E (THM.13 part 3): Bootstrap components in `ride`: axe and the keyboard (spec 14 §14.6).

The `ride` styleguide shows the Bootstrap components (buttons, forms, alerts, badges, cards,
pagination, a dropdown and a modal) in the look of the theme. axe `wcag22aa` is clean at 360, 768
and 1280 px, and a modal and a dropdown open and close with the keyboard alone without any
`securitypolicyviolation` (the bundle sets styles through the CSSOM, never through markup).
"""

import pytest
from playwright.sync_api import Page, expect
from pytest_django.fixtures import Settings as SettingsWrapper
from pytest_django.live_server_helper import LiveServer

from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import csp_violations
from tests.e2e.test_styleguide import open_styleguide

pytestmark = pytest.mark.django_db(transaction=True)


def open_ride(
    page: Page, live_server: LiveServer, settings: SettingsWrapper, width: int = 1280
) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    page.emulate_media(reduced_motion="reduce")
    page.set_viewport_size({"width": width, "height": 900})
    open_styleguide(page, live_server)
    expect(page.locator("html")).to_have_attribute("data-theme", "ride")
    page.evaluate("() => document.fonts.ready")


@pytest.mark.parametrize("width", [360, 768, 1280])
def test_axe_is_clean_on_the_ride_styleguide_with_bootstrap_components(
    width: int, page: Page, live_server: LiveServer, settings: SettingsWrapper
) -> None:
    open_ride(page, live_server, settings, width)
    expect(page.locator("#sg-foundation")).to_be_visible()
    violations = run_axe(page)
    assert violations == [], format_violations(violations)
    assert csp_violations(page) == [], "\n".join(csp_violations(page))


def test_the_dropdown_opens_and_closes_with_the_keyboard(
    page: Page, live_server: LiveServer, settings: SettingsWrapper
) -> None:
    open_ride(page, live_server, settings)
    toggle = page.locator("#sg-dropdown-toggle")
    toggle.focus()
    page.keyboard.press("Enter")
    expect(toggle).to_have_attribute("aria-expanded", "true")
    expect(page.locator("#sg-dropdown-menu")).to_be_visible()
    page.keyboard.press("ArrowDown")
    expect(page.locator("#sg-dropdown-menu .dropdown-item").first).to_be_focused()
    page.keyboard.press("Escape")
    expect(toggle).to_have_attribute("aria-expanded", "false")
    expect(page.locator("#sg-dropdown-menu")).to_be_hidden()
    expect(toggle).to_be_focused()
    violations = run_axe(page)
    assert violations == [], format_violations(violations)
    assert csp_violations(page) == [], "\n".join(csp_violations(page))


def test_the_modal_opens_traps_focus_and_closes_with_the_keyboard(
    page: Page, live_server: LiveServer, settings: SettingsWrapper
) -> None:
    open_ride(page, live_server, settings)
    opener = page.locator("#sg-modal-open")
    opener.focus()
    page.keyboard.press("Enter")
    modal = page.locator("#sg-modal")
    expect(modal).to_be_visible()
    expect(modal).to_have_attribute("aria-modal", "true")
    for _ in range(6):  # Tab never leaves the dialog
        page.keyboard.press("Tab")
        assert page.evaluate("() => !!document.activeElement.closest('#sg-modal')")
    violations = run_axe(page)
    assert violations == [], format_violations(violations)
    page.keyboard.press("Escape")
    expect(modal).to_be_hidden()
    expect(opener).to_be_focused()
    assert csp_violations(page) == [], "\n".join(csp_violations(page))


@pytest.mark.parametrize(
    "selector",
    ["#sg-foundation .btn-primary", "#sg-foundation .form-control", "#sg-foundation a.page-link"],
)
def test_focus_rings_of_bootstrap_components_are_visible(
    selector: str, page: Page, live_server: LiveServer, settings: SettingsWrapper
) -> None:
    open_ride(page, live_server, settings)
    page.locator(selector).first.focus()
    page.keyboard.press("Shift+Tab")
    page.keyboard.press("Tab")
    style = page.evaluate(
        "() => { const s = getComputedStyle(document.activeElement);"
        " return {width: parseFloat(s.outlineWidth), style: s.outlineStyle}; }"
    )
    assert style["style"] == "solid"
    assert style["width"] >= 2
