"""E2E (FIT.7 Done-when): the garage state survives reload, language switch and back button.

A visitor with two saved vehicles changes the active one, switches language and walks the
history back and forward; the active vehicle is the same everywhere and no URL of the whole
flow carries a vehicle name or a garage parameter.
"""

import re

import pytest
from playwright.sync_api import Locator, Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.fitment import garage
from apps.fitment.tests.factories import FitmentNodeFactory, FitmentSchemeFactory
from tests.i18n import other_language

pytestmark = pytest.mark.django_db(transaction=True)

_FORBIDDEN_IN_URL = re.compile(
    r"acme|roadster|tourer|vehicle|garage_|active|garage=|make=|model=", re.IGNORECASE
)


def _menu(page: Page) -> Locator:
    return page.locator("[data-garage-menu] summary")


def test_the_garage_survives_reload_language_switch_and_back(
    page: Page, live_server: LiveServer
) -> None:
    FitmentSchemeFactory.create()
    make = FitmentNodeFactory.create(name="Acme")
    FitmentNodeFactory.create(name="Roadster", parent=make)
    FitmentNodeFactory.create(name="Tourer", parent=make)
    value = garage.Garage((garage.Vehicle("Acme", "Roadster"), garage.Vehicle("Acme", "Tourer")), 0)
    cookie = garage.dumps(value)
    page.context.add_cookies(
        [{"name": garage.COOKIE_NAME, "value": cookie, "url": live_server.url}]
    )
    visited: list[str] = []
    page.on("framenavigated", lambda frame: visited.append(frame.url))

    page.goto(f"{live_server.url}/?s=zzz")
    expect(_menu(page)).to_contain_text("Acme, Roadster")

    # Choose the second vehicle through the chooser (the keyboard flow is proven elsewhere).
    _menu(page).click()
    page.locator("[data-garage-menu] a").first.click()
    page.get_by_role("radio", name="Acme, Tourer").check()
    page.locator("form[action*='garage/apply'] button[type=submit]").click()
    expect(_menu(page)).to_contain_text("Acme, Tourer")

    # Reload keeps it.
    page.reload()
    expect(_menu(page)).to_contain_text("Acme, Tourer")

    # Language switch keeps it, and the vehicle is the same one.
    second = other_language()
    page.locator("nav.language-switcher").locator(f'button[value="{second}"]').click()
    expect(page.locator("html")).to_have_attribute("lang", re.compile(f"^{second}"))
    expect(_menu(page)).to_contain_text("Acme, Tourer")

    # Walk the history back to the first page, loaded while Roadster was active on the server:
    # it must show the vehicle chosen since (Tourer), not the one it was rendered with.
    for _ in range(8):
        if "s=zzz" in page.url:
            break
        page.go_back()
    assert "s=zzz" in page.url
    expect(_menu(page)).to_contain_text("Acme, Tourer")
    page.go_forward()
    expect(_menu(page)).to_contain_text("Acme, Tourer")

    assert len(visited) >= 5
    for url in visited:
        assert not _FORBIDDEN_IN_URL.search(url), url
