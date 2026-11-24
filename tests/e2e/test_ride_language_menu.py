"""E2E (THM.35): the `ride` compact language dropdown (spec 14 §14.6).

The toggle opens and closes the list from the keyboard, Tab moves between the items and out of the
list closes it, Escape closes it and returns focus to the toggle; axe finds nothing with the list
open (AA contrast under the default palette), and the list is a light panel that readable text
sits on. The visual baselines are in test_visual_baselines.py.
"""

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from tests.e2e.test_ride_overlays import _ride, clean, open_page  # noqa: F401 (fixtures)
from tests.i18n import other_language

pytestmark = pytest.mark.django_db(transaction=True)

TOGGLE = ".language-menu__toggle"
LIST = ".language-menu__list"


def test_the_toggle_is_named_language_and_follows_the_list(
    page: Page, live_server: LiveServer
) -> None:
    open_page(page, live_server, "/", 1280)
    toggle = page.locator(TOGGLE)
    expect(toggle).to_have_attribute("aria-expanded", "false")
    expect(page.locator(LIST)).to_be_hidden()
    assert page.get_by_role("button", name="Language: ").count() == 1
    toggle.focus()
    page.keyboard.press("Enter")
    expect(toggle).to_have_attribute("aria-expanded", "true")
    expect(page.locator(LIST)).to_be_visible()
    page.keyboard.press("Space")
    expect(toggle).to_have_attribute("aria-expanded", "false")
    expect(page.locator(LIST)).to_be_hidden()


def test_tab_moves_between_items_tab_out_closes_and_escape_returns_focus(
    page: Page, live_server: LiveServer
) -> None:
    open_page(page, live_server, "/", 1280)
    toggle = page.locator(TOGGLE)
    toggle.focus()
    page.keyboard.press("Enter")
    other = page.locator(f'{LIST} button[value="{other_language()}"]')
    page.keyboard.press("Tab")
    expect(other).to_be_focused()
    page.keyboard.press("Escape")
    expect(toggle).to_be_focused()
    expect(toggle).to_have_attribute("aria-expanded", "false")
    page.keyboard.press("Enter")
    enabled = page.locator(f"{LIST} button:not([disabled])").count()
    for _ in range(enabled):  # onto each item in turn, then out of the menu
        page.keyboard.press("Tab")
    expect(page.locator(LIST)).to_be_visible()
    page.keyboard.press("Tab")
    expect(page.locator(LIST)).to_be_hidden()
    expect(toggle).to_have_attribute("aria-expanded", "false")


def test_the_open_list_is_axe_clean_and_its_text_is_readable(
    page: Page, live_server: LiveServer
) -> None:
    open_page(page, live_server, "/", 1280)
    page.locator(TOGGLE).click()
    expect(page.locator(f"{LIST} button").first).to_be_visible()
    colours = page.evaluate(
        """() => {
          const list = document.querySelector('.language-menu__list');
          const bg = getComputedStyle(list).backgroundColor;
          return [...document.querySelectorAll('.language-menu__item')].map(
            (el) => [getComputedStyle(el).color, bg]);
        }"""
    )
    assert all(colour != background for colour, background in colours)
    clean(page, include=".language-menu")
    page.mouse.click(5, 400)
    expect(page.locator(LIST)).to_be_hidden()


def test_the_phone_toggle_in_the_drawer_switches_language(
    page: Page, live_server: LiveServer
) -> None:
    open_page(page, live_server, "/", 375)
    page.get_by_role("button", name="Menu").click()
    page.locator(TOGGLE).click()
    clean(page, include="#site-offcanvas")
    second = other_language()
    page.locator(f'{LIST} button[value="{second}"]').click()
    expect(page.locator("html")).to_have_attribute("lang", second)


def test_without_the_script_the_list_stays_open_and_usable(
    page: Page, live_server: LiveServer
) -> None:
    page.route("**/language-menu.js", lambda route: route.abort())
    open_page(page, live_server, "/", 1280)
    expect(page.locator(TOGGLE)).to_be_hidden()
    second = other_language()
    expect(page.locator(f'{LIST} button[value="{second}"]')).to_be_visible()
    page.locator(f'{LIST} button[value="{second}"]').click()
    expect(page.locator("html")).to_have_attribute("lang", second)
