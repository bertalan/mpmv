"""E2E (THM.14 part 1): the `ride` carousels on the styleguide (spec 14 §14.6, §14.9).

Keyboard only (Tab, Enter, Space, arrows), `aria-roledescription`, no autoplay unless enabled
(then paused on hover and focus), nothing moves under `prefers-reduced-motion`, readable
without JavaScript, axe `wcag22aa` and zero CSP violations at 360, 768 and 1280 px.
"""

import pytest
from playwright.sync_api import Browser, Page, expect
from pytest_django.fixtures import Settings as SettingsWrapper
from pytest_django.live_server_helper import LiveServer

from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import csp_violations
from tests.e2e.test_ride_bootstrap_components import open_ride
from tests.e2e.test_styleguide import open_styleguide
from tests.i18n import translated

pytestmark = pytest.mark.django_db(transaction=True)

STRIP = "#sg-strip"
HERO = "#sg-hero-slider"
AUTO = "#sg-strip-auto"
GALLERY = "#sg-gallery-carousel"


def active_label(page: Page, root: str) -> str:
    label: str = page.evaluate(
        "(root) => document.querySelector(`${root} .carousel-item.active`)"
        ".getAttribute('aria-label')",
        root,
    )
    return label


@pytest.mark.parametrize("width", [360, 768, 1280])
def test_axe_and_csp_are_clean_with_the_carousels(
    width: int, page: Page, live_server: LiveServer, settings: SettingsWrapper
) -> None:
    open_ride(page, live_server, settings, width)
    for root in (STRIP, HERO, GALLERY):
        expect(page.locator(root)).to_have_attribute("aria-roledescription", "carousel")
    violations = run_axe(page)
    assert violations == [], format_violations(violations)
    assert csp_violations(page) == [], "\n".join(csp_violations(page))


def test_the_strip_works_with_the_keyboard_alone(
    page: Page, live_server: LiveServer, settings: SettingsWrapper
) -> None:
    open_ride(page, live_server, settings)
    page.locator(f"{STRIP} [data-bs-slide=next]").focus()
    assert active_label(page, STRIP) == "1 of 3"
    page.keyboard.press("Enter")
    expect(page.locator(f"{STRIP} .carousel-item.active")).to_have_attribute("aria-label", "2 of 3")
    page.keyboard.press("Space")
    expect(page.locator(f"{STRIP} .carousel-item.active")).to_have_attribute("aria-label", "3 of 3")
    page.keyboard.press("ArrowLeft")
    expect(page.locator(f"{STRIP} .carousel-item.active")).to_have_attribute("aria-label", "2 of 3")
    page.keyboard.press("ArrowRight")
    expect(page.locator(f"{STRIP} .carousel-item.active")).to_have_attribute("aria-label", "3 of 3")
    indicator = page.locator(f"{STRIP} [data-bs-slide-to='0']")
    indicator.focus()
    page.keyboard.press("Enter")
    expect(page.locator(f"{STRIP} .carousel-item.active")).to_have_attribute("aria-label", "1 of 3")
    expect(indicator).to_have_attribute("aria-current", "true")
    assert csp_violations(page) == []


def test_the_hero_slider_and_the_gallery_thumbnails_use_buttons(
    page: Page, live_server: LiveServer, settings: SettingsWrapper
) -> None:
    open_ride(page, live_server, settings)
    page.locator(f"{HERO} [data-bs-slide=next]").focus()
    page.keyboard.press("Enter")
    expect(page.locator(f"{HERO} .carousel-item.active")).to_have_attribute("aria-label", "2 of 3")
    thumb = page.locator(f"{GALLERY} [data-bs-slide-to='2']")
    thumb.focus()
    page.keyboard.press("Space")
    expect(page.locator(f"{GALLERY} .carousel-item.active")).to_have_attribute(
        "aria-label", "3 of 3"
    )
    expect(thumb).to_have_attribute("aria-current", "true")


def test_no_autoplay_unless_enabled_and_the_toggle_starts_and_stops_it(
    page: Page, live_server: LiveServer, settings: SettingsWrapper
) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    page.emulate_media(reduced_motion="no-preference")
    page.set_viewport_size({"width": 1280, "height": 900})
    open_styleguide(page, live_server)
    page.wait_for_timeout(1800)  # the styleguide strip rotates every 1 s when enabled
    assert active_label(page, STRIP) == "1 of 3"
    auto = page.locator(AUTO)
    toggle = auto.locator("[data-ride-toggle]")
    expect(toggle).to_have_text(translated("Pause"))
    toggle.focus()  # focus inside holds the rotation
    page.wait_for_timeout(900)  # a slide already moving finishes first
    label = active_label(page, AUTO)
    page.wait_for_timeout(1500)
    assert active_label(page, AUTO) == label
    page.keyboard.press("Enter")
    expect(toggle).to_have_text(translated("Play"))
    page.locator("h1, h2").first.focus()
    page.mouse.move(0, 0)
    page.wait_for_timeout(900)
    label = active_label(page, AUTO)
    page.wait_for_timeout(1500)
    assert active_label(page, AUTO) == label


def test_reduced_motion_stops_autoplay_and_every_slide_transition(
    page: Page, live_server: LiveServer, settings: SettingsWrapper
) -> None:
    open_ride(page, live_server, settings)  # reduced motion on
    label = active_label(page, AUTO)
    page.wait_for_timeout(1800)
    assert active_label(page, AUTO) == label
    expect(page.locator(f"{AUTO} [data-ride-toggle]")).to_be_hidden()
    duration = page.evaluate(
        "() => getComputedStyle(document.querySelector('#sg-strip .carousel-item'))"
        ".transitionDuration"
    )
    assert duration in {"0s", ""}


def test_without_javascript_every_slide_and_item_is_readable(
    browser: Browser, live_server: LiveServer, settings: SettingsWrapper, page: Page
) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    open_styleguide(page, live_server)  # logs in; its session cookie moves to the no-JS context
    state = page.context.storage_state()
    context = browser.new_context(java_script_enabled=False, storage_state=state)
    bare = context.new_page()
    bare.goto(f"{live_server.url}/_styleguide/")
    for name in ("Touring jacket", "Winter gloves", "Helmet visor"):
        expect(bare.locator(STRIP).get_by_role("link", name=name)).to_be_visible()
    expect(bare.locator(f"{STRIP} [data-bs-slide=next]")).to_be_hidden()
    expect(bare.locator(f"{HERO} .carousel-item").nth(2)).to_be_visible()
    expect(bare.locator(f"{GALLERY} .carousel-item").nth(2)).to_be_visible()
    context.close()
