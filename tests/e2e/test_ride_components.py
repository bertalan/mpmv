"""E2E (THM.9 part 2): `ride` carousels, consent banner and consent-gated video modal.

A CMS page with a testimonial strip (rotation enabled), a blog-card style strip and a video
block, a new visitor (so the consent banner shows). axe `wcag22aa` and zero CSP violations at 360,
768 and 1280 px with the banner open; the carousel works with the keyboard and its Pause button;
reduced motion means no rotation; no focused element is ever behind the banner or the sticky
header; the video modal injects no `<iframe>` before consent and does after it.
"""

import re

import pytest
from playwright.sync_api import Page, expect
from pytest_django.fixtures import Settings as SettingsWrapper
from pytest_django.live_server_helper import LiveServer

from apps.cms.models import HomePage
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations
from tests.e2e.test_consent_banner import consent_script_ready

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.consent_banner]

QUOTES = [
    {"type": "testimonial", "value": {"quote": f"Quote {n}", "author": f"Rider {n}", "role": ""}}
    for n in range(1, 5)
]
STRIP = "#strip-1"


@pytest.fixture
def _components_home(live_server: LiveServer, _cms_site: None) -> None:
    home = HomePage.objects.get(locale__language_code="en")
    home.body = [
        (
            "strip",
            {
                "heading": "Voices",
                "cards": QUOTES,
                "per_view": "2",
                "autoplay": True,
                "interval": 3000,
            },
        ),
        (
            "video_modal",
            {"title": "Workshop tour", "url": "https://www.youtube.com/watch?v=abcdefghijk"},
        ),
    ]
    home.save_revision().publish()


def open_home(
    page: Page, live_server: LiveServer, settings: SettingsWrapper, width: int, motion: str
) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    page.emulate_media(reduced_motion=motion)  # type: ignore[arg-type]
    page.set_viewport_size({"width": width, "height": 800})
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    page.goto(f"{live_server.url}/")
    page.wait_for_load_state("load")
    consent_script_ready(page)
    expect(page.locator("#consent-banner")).to_be_visible()


def active(page: Page) -> str:
    label: str = page.evaluate(
        "(root) => document.querySelector(`${root} .carousel-item.active`)"
        ".getAttribute('aria-label')",
        STRIP,
    )
    return label


@pytest.mark.usefixtures("_components_home")
@pytest.mark.parametrize("width", [360, 768, 1280])
def test_axe_and_csp_are_clean_with_the_banner_and_the_carousel(
    width: int, page: Page, live_server: LiveServer, settings: SettingsWrapper
) -> None:
    open_home(page, live_server, settings, width, "reduce")
    expect(page.locator("#consent-banner")).to_have_class(re.compile(r"\balert\b"))
    expect(page.locator(STRIP)).to_have_attribute("aria-roledescription", "carousel")
    violations = run_axe(page)
    assert violations == [], format_violations(violations)
    assert csp_violations(page) == [], "\n".join(csp_violations(page))


@pytest.mark.usefixtures("_components_home")
def test_the_carousel_rotates_pauses_and_follows_the_keyboard(
    page: Page, live_server: LiveServer, settings: SettingsWrapper
) -> None:
    open_home(page, live_server, settings, 1280, "no-preference")
    toggle = page.locator(f"{STRIP} [data-ride-toggle]")
    expect(toggle).to_have_text("Pause")
    expect(page.locator(f"{STRIP} .carousel-item.active")).not_to_have_attribute(
        "aria-label", "1 of 2", timeout=9000
    )
    toggle.focus()  # focus inside holds the rotation
    page.keyboard.press("Enter")
    expect(toggle).to_have_text("Play")
    page.locator("#consent-banner button").first.focus()
    page.mouse.move(0, 0)
    page.wait_for_timeout(1000)  # a slide already moving finishes first
    label = active(page)
    page.wait_for_timeout(6500)
    assert active(page) == label
    page.locator(f"{STRIP} [data-bs-slide=next]").focus()
    page.keyboard.press("Enter")
    expect(page.locator(f"{STRIP} .carousel-item.active")).not_to_have_attribute(
        "aria-label", label
    )


@pytest.mark.usefixtures("_components_home")
def test_reduced_motion_means_no_rotation_and_no_play_button(
    page: Page, live_server: LiveServer, settings: SettingsWrapper
) -> None:
    open_home(page, live_server, settings, 1280, "reduce")
    label = active(page)
    page.wait_for_timeout(6500)
    assert active(page) == label
    expect(page.locator(f"{STRIP} [data-ride-toggle]")).to_be_hidden()


@pytest.mark.usefixtures("_components_home")
@pytest.mark.parametrize("width", [360, 1280])
def test_a_focused_element_is_never_behind_the_banner_or_the_sticky_header(
    width: int, page: Page, live_server: LiveServer, settings: SettingsWrapper
) -> None:
    open_home(page, live_server, settings, width, "reduce")
    consent_script_ready(page)
    banner_top = page.locator("#consent-banner").bounding_box()["y"]  # type: ignore[index]
    header = page.locator("header.site-header").first
    for _ in range(70):
        page.keyboard.press("Tab")
        info = page.evaluate(
            """() => { const el = document.activeElement; const box = el.getBoundingClientRect();
              const head = document.querySelector('header.site-header').getBoundingClientRect();
              return {inBanner: !!el.closest('#consent-banner'), top: box.top, bottom: box.bottom,
                      header: head.bottom, skip: el === document.body
                        || el.getAttribute('href') === '#main',
                      name: (el.textContent || el.tagName).trim()}; }"""
        )
        if info["inBanner"]:
            continue
        if info["skip"]:
            continue  # the skip link is fixed on purpose; body means the tab ring wrapped
        # a region taller than the free space only needs its start (where the ring is) in view
        free = banner_top - info["header"]
        fits = info["bottom"] - info["top"] <= free
        assert not fits or info["bottom"] <= banner_top + 1, (
            f"{info['name']!r} sits under the banner"
        )
        name = info["name"]
        assert info["top"] < banner_top - 8, f"{name!r} starts under the banner"
        sticky = page.evaluate(
            "() => getComputedStyle(document.querySelector('header.site-header')).position"
        )
        if sticky in {"sticky", "fixed"} and header.is_visible():
            inside = page.evaluate("() => !!document.activeElement.closest('header.site-header')")
            if not inside:
                name = info["name"]
                assert info["top"] >= info["header"] - 1, f"{name!r} sits under the header"


@pytest.mark.usefixtures("_components_home")
def test_the_video_modal_injects_no_iframe_before_consent_and_does_after(
    page: Page, live_server: LiveServer, settings: SettingsWrapper
) -> None:
    open_home(page, live_server, settings, 1280, "reduce")
    consent_script_ready(page)
    page.locator(".ride-video__open").click()
    expect(page.locator("[data-video-gate]")).to_be_visible()
    expect(page.locator("[data-video-gate] a[href*='youtube']")).to_be_visible()
    expect(page.locator("[data-video] iframe")).to_have_count(0)
    page.keyboard.press("Escape")
    expect(page.locator("#consent-banner")).to_be_visible()  # never covered by the closed modal
    page.get_by_role("button", name="Accept all").click()
    expect(page.locator("#consent-banner")).to_be_hidden()
    page.locator(".ride-video__open").click()
    expect(page.locator("[data-video] iframe")).to_have_attribute(
        "src", "https://www.youtube-nocookie.com/embed/abcdefghijk"
    )
    expect(page.locator("[data-video-gate]")).to_be_hidden()
    assert csp_violations(page) == [], "\n".join(csp_violations(page))
    # The frame may take focus (a cross-origin frame swallows keys), so aim Escape at the modal.
    page.locator("[data-video] .modal.show").focus()
    page.keyboard.press("Escape")
    expect(page.locator("[data-video] .modal")).to_be_hidden()
    expect(page.locator("[data-video] iframe")).to_have_count(0)
    page.locator(".ride-video__open").click()
    expect(page.locator("[data-video] iframe")).to_have_count(1)
    page.locator("[data-video] .modal.show [data-bs-dismiss='modal']").first.click()
    expect(page.locator("[data-video] .modal")).to_be_hidden()
    expect(page.locator("[data-video] iframe")).to_have_count(0)
