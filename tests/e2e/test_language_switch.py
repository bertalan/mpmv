"""E2E (I18N.1): `/` is the primary language, the others have a prefix; the switch stays put."""

import re
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.core.i18n import configured_languages, primary_language
from tests.i18n import language_prefix

pytestmark = pytest.mark.django_db(transaction=True)


HOMES = [(f"{language_prefix(code)}/", code) for code in configured_languages()]


@pytest.mark.parametrize(("path", "language"), HOMES)
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
    shot(primary_language())
    for code in configured_languages():
        if code == primary_language():
            continue
        page.locator(f'button[value="{code}"]').click()
        expect(page).to_have_url(re.compile(rf"/{code}/\?page=2$"))
        expect(page.locator("html")).to_have_attribute("lang", code)
        shot(code)
    page.locator(f'button[value="{primary_language()}"]').click()
    expect(page).to_have_url(re.compile(r"^http://[^/]+/\?page=2$"))
    expect(page.locator("html")).to_have_attribute("lang", primary_language())
    shot("back-to-primary")
