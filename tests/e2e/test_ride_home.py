"""E2E (THM.6): the home page with the `ride` theme (spec 14 §14.3, §14.6, §14.11).

Desktop and mobile: screenshots for the owner's visual review, zero axe violations
(`wcag22aa`), no CSP violation, no request to another origin, the self-hosted fonts load, the
burger menu opens below 64rem and the page never scrolls sideways.
"""

from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.fixtures import Settings as SettingsWrapper
from pytest_django.live_server_helper import LiveServer

from apps.cms.models import HomePage
from apps.core.tests.factories import MediaImageFactory
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)

NO_SIDEWAYS_SCROLL = "() => document.documentElement.scrollWidth <= window.innerWidth"


@pytest.fixture(autouse=True)
def _ride_home(settings: SettingsWrapper, live_server: LiveServer, _cms_site: None) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    settings.MPMV_HELP_URL = "/about/#help"
    home = HomePage.objects.get(locale__language_code="en")
    banner = MediaImageFactory.create(title="Banner", file__filename="banner.png")
    home.body = [
        (
            "hero",
            {
                "heading": "Ride better",
                "text": "Gear and parts from small workshops, sold by the people who make them.",
                "image": banner,
                "alt_text": "A rider on a mountain road",
                "links": [
                    {"page": None, "url": "/about/", "label": "Shop now"},
                    {"page": None, "url": "/about/", "label": "Sell with us"},
                ],
            },
        ),
        ("paragraph", "<p>Welcome to the shop.</p>"),
        (
            "cta",
            {
                "heading": "Sell with us",
                "text": "Open your shop in a day.",
                "link": {"page": None, "url": "/about/", "label": "Apply now"},
            },
        ),
    ]
    home.save_revision().publish()


def open_home(page: Page, live_server: LiveServer, width: int, height: int) -> list[str]:
    foreign: list[str] = []
    origin = live_server.url
    page.on(
        "request",
        lambda request: (
            foreign.append(request.url)
            if not request.url.startswith((origin, "data:", "blob:"))
            else None
        ),
    )
    page.emulate_media(reduced_motion="reduce")
    page.set_viewport_size({"width": width, "height": height})
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    response = page.goto(f"{live_server.url}/")
    assert response is not None
    assert response.status == 200
    page.wait_for_load_state("load")
    page.evaluate("() => document.fonts.ready")
    return foreign


@pytest.mark.parametrize("width", [360, 768, 1280])
def test_the_home_page_renders_with_ride_at_every_width(
    width: int, page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    foreign = open_home(page, live_server, width, 900)
    expect(page.locator("html")).to_have_attribute("data-theme", "ride")
    expect(page.locator("main h1")).to_have_count(1)
    expect(page.locator("main h1")).to_have_text("Ride better")
    expect(page.locator(".site-footer__col")).to_have_count(3)
    assert page.evaluate("() => document.fonts.check(\"700 1em 'Barlow Condensed'\")")
    assert page.evaluate("() => document.fonts.check(\"400 1em 'Barlow'\")")
    assert page.evaluate(NO_SIDEWAYS_SCROLL)
    toggle = page.locator(".nav-toggle")
    if width >= 1024:  # 64rem
        expect(page.get_by_role("navigation", name="Main")).to_be_visible()
        expect(toggle).to_be_hidden()
    else:
        expect(toggle).to_be_visible()
    violations = run_axe(page)
    assert violations == [], format_violations(violations)
    assert foreign == [], foreign
    assert csp_violations(page) == [], "\n".join(csp_violations(page))
    shot(str(width))


@pytest.mark.parametrize("width", [360, 768])
def test_the_burger_menu_opens_and_closes_below_64rem(
    width: int, page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    open_home(page, live_server, width, 800)
    toggle = page.locator(".nav-toggle")
    first_link = page.get_by_role("navigation", name="Main").get_by_role("link").first
    expect(toggle).to_have_attribute("aria-expanded", "false")
    expect(first_link).to_be_hidden()
    toggle.click()
    expect(toggle).to_have_attribute("aria-expanded", "true")
    expect(first_link).to_be_visible()
    shot(f"{width}-menu-open")
    page.keyboard.press("Escape")
    expect(toggle).to_have_attribute("aria-expanded", "false")
    assert page.evaluate(NO_SIDEWAYS_SCROLL)


def test_buttons_and_links_are_at_least_44px_high_in_the_hero(
    page: Page, live_server: LiveServer
) -> None:
    open_home(page, live_server, 1280, 900)
    for button in page.locator(".hero .button").all():
        box = button.bounding_box()
        assert box is not None
        assert box["height"] >= 44, box


def test_the_skip_link_is_the_first_stop_and_focus_is_visible(
    page: Page, live_server: LiveServer
) -> None:
    open_home(page, live_server, 1280, 900)
    page.keyboard.press("Tab")
    focused = page.evaluate(
        "() => { const e = document.activeElement; const s = getComputedStyle(e);"
        " return {cls: e.className, width: s.outlineWidth, style: s.outlineStyle}; }"
    )
    assert focused["cls"] == "skip-link"
    assert focused["style"] != "none"
    assert float(focused["width"].removesuffix("px")) >= 2


def test_header_controls_are_at_least_44px_high(page: Page, live_server: LiveServer) -> None:
    open_home(page, live_server, 360, 800)
    box = page.locator(".nav-toggle").bounding_box()
    assert box is not None
    assert box["height"] >= 44, box
    page.set_viewport_size({"width": 1280, "height": 900})
    for link in page.locator(".site-header .site-nav a, .site-header .help-link").all():
        box = link.bounding_box()
        assert box is not None
        assert box["height"] >= 44, (link.inner_text(), box)
