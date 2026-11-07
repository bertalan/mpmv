"""E2E (I18N.1): `/` is EN, `/it/` and `/de/` are active, and the switcher keeps the page."""

import re
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize(("path", "language"), [("/", "en"), ("/it/", "it"), ("/de/", "de")])
def test_each_language_home_is_served(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path], path: str, language: str
) -> None:
    page.goto(f"{live_server.url}{path}")
    expect(page.locator("html")).to_have_attribute("lang", language)
    expect(page.locator(f'button[value="{language}"]')).to_have_attribute("aria-current", "true")
    shot(language)


def test_visitor_switches_language_and_stays_on_the_page(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page.goto(f"{live_server.url}/?page=2")
    shot("en")
    page.locator('button[value="it"]').click()
    expect(page).to_have_url(re.compile(r"/it/\?page=2$"))
    expect(page.locator("html")).to_have_attribute("lang", "it")
    shot("it")
    page.locator('button[value="de"]').click()
    expect(page).to_have_url(re.compile(r"/de/\?page=2$"))
    expect(page.locator("html")).to_have_attribute("lang", "de")
    shot("de")
    page.locator('button[value="en"]').click()
    expect(page).to_have_url(re.compile(r"^http://[^/]+/\?page=2$"))
    expect(page.locator("html")).to_have_attribute("lang", "en")
    shot("back-to-en")
