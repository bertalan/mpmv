"""E2E (THM.14 part 3/3): the `ride` home with a slider hero and a strip built in the CMS.

axe `wcag22aa`, zero CSP violations and the keyboard at 360, 768 and 1280 px; the slider keeps
the page's one `<h1>`, does not move on its own and a phone shows one strip item at a time.
"""

import pytest
from playwright.sync_api import Page, expect
from pytest_django.fixtures import Settings as SettingsWrapper
from pytest_django.live_server_helper import LiveServer

from apps.cms.models import HomePage
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)

QUOTES = [
    {"type": "testimonial", "value": {"quote": f"Quote {n}", "author": f"Rider {n}", "role": ""}}
    for n in range(1, 6)
]

SLIDE_A: dict[str, object] = {
    "image": None,
    "alt_text": "",
    "heading": "Spring sale",
    "text": "",
    "links": [],
}
SLIDE_B: dict[str, object] = {
    "image": None,
    "alt_text": "",
    "heading": "New vendors",
    "text": "",
    "links": [],
}


@pytest.fixture(autouse=True)
def _slider_home(settings: SettingsWrapper, live_server: LiveServer, _cms_site: None) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    settings.MPMV_HELP_URL = "/about/#help"
    home = HomePage.objects.get(locale__language_code="en")
    home.body = [
        (
            "hero",
            {
                "heading": "Ride better",
                "text": "Gear from small workshops.",
                "image": None,
                "alt_text": "",
                "layout": "slider",
                "slides": [
                    {
                        "image": None,
                        "alt_text": "",
                        "heading": "Spring sale",
                        "text": "",
                        "links": [],
                    },
                    {
                        "image": None,
                        "alt_text": "",
                        "heading": "New vendors",
                        "text": "",
                        "links": [],
                    },
                ],
                "links": [{"page": None, "url": "/about/", "label": "Shop now"}],
            },
        ),
        ("strip", {"heading": "Voices", "cards": QUOTES, "per_view": "4"}),
    ]
    home.save_revision().publish()


def open_home(page: Page, live_server: LiveServer, width: int) -> None:
    page.set_viewport_size({"width": width, "height": 900})
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    page.goto(f"{live_server.url}/")
    page.wait_for_load_state("load")


@pytest.mark.parametrize("width", [360, 768, 1280])
def test_the_slider_home_is_axe_and_csp_clean_with_one_h1(
    width: int, page: Page, live_server: LiveServer
) -> None:
    open_home(page, live_server, width)
    expect(page.locator("h1")).to_have_count(1)
    expect(page.locator("#hero-slider-1")).to_have_attribute("aria-roledescription", "carousel")
    violations = run_axe(page)
    assert violations == [], format_violations(violations)
    assert csp_violations(page) == [], "\n".join(csp_violations(page))


def test_the_slider_works_with_the_keyboard_and_does_not_move_on_its_own(
    page: Page, live_server: LiveServer
) -> None:
    open_home(page, live_server, 1280)
    slider = page.locator("#hero-slider-1")
    active = slider.locator(".carousel-item.active")
    expect(active).to_have_attribute("aria-label", "1 of 3")
    page.wait_for_timeout(1500)
    expect(active).to_have_attribute("aria-label", "1 of 3")
    slider.locator("[data-bs-slide=next]").focus()
    page.keyboard.press("Enter")
    expect(slider.locator(".carousel-item.active")).to_have_attribute("aria-label", "2 of 3")


def test_a_phone_shows_one_strip_item_per_view(page: Page, live_server: LiveServer) -> None:
    open_home(page, live_server, 360)
    items = page.locator(".ride-strip .carousel-item.active .ride-strip__item")
    first, second = items.nth(0).bounding_box(), items.nth(1).bounding_box()
    assert first is not None
    assert second is not None
    assert first["width"] >= 300  # nearly the whole viewport
    assert second["x"] >= first["x"] + first["width"] - 1  # beside, not below
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
