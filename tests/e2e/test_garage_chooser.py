"""E2E (FIT.7 1/n): the header garage menu and the chooser, keyboard only.

A visitor with two saved vehicles opens the header menu with the keyboard, reaches the chooser,
picks a vehicle with the arrow keys and presses Apply; the page returns with the new state
announced in the polite region. Reload keeps it; no URL ever holds garage state. axe (WCAG 2.2
AA) and the strict CSP find nothing wrong.
"""

from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.fitment import garage
from apps.fitment.tests.factories import FitmentNodeFactory, FitmentSchemeFactory
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)


def _accessible(page: Page) -> None:
    violations = run_axe(page)
    assert not violations, format_violations(violations)
    assert csp_violations(page) == []


def test_a_visitor_changes_vehicle_with_the_keyboard(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    FitmentSchemeFactory.create()
    make = FitmentNodeFactory.create(name="Acme")
    FitmentNodeFactory.create(name="Roadster", parent=make)
    FitmentNodeFactory.create(name="Tourer", parent=make)
    value = garage.Garage((garage.Vehicle("Acme", "Roadster"), garage.Vehicle("Acme", "Tourer")), 0)
    from django.http import HttpResponse

    from apps.fitment.views import _write

    cookie = _write(HttpResponse(), value).cookies[garage.COOKIE_NAME].value
    page.context.add_cookies(
        [{"name": garage.COOKIE_NAME, "value": cookie, "url": live_server.url}]
    )
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    page.goto(f"{live_server.url}/")
    summary = page.locator("[data-garage-menu] summary")
    expect(summary).to_have_text("Garage: Acme, Roadster")
    _accessible(page)
    shot("header")

    summary.focus()
    page.keyboard.press("Enter")
    page.keyboard.press("Tab")
    page.keyboard.press("Enter")
    expect(page.get_by_role("heading", level=1, name="Garage")).to_be_visible()
    assert "vehicle" not in page.url
    assert "Roadster" not in page.url
    _accessible(page)
    shot("chooser")

    page.get_by_role("radio", name="Acme, Roadster").focus()
    page.keyboard.press("ArrowDown")  # the radio group: next is the second vehicle
    expect(page.get_by_role("radio", name="Acme, Tourer")).to_be_checked()
    page.keyboard.press("Tab")
    page.keyboard.press("Enter")  # Apply
    expect(page.locator("[data-garage-menu] summary")).to_have_text("Garage: Acme, Tourer")
    expect(
        page.locator(".site-messages [aria-live=polite], .site-messages [role=status]")
    ).to_contain_text("Acme, Tourer is now your active vehicle")
    assert page.url == f"{live_server.url}/en/" or page.url.rstrip("/") == live_server.url
    shot("applied")

    page.reload()
    expect(page.locator("[data-garage-menu] summary")).to_have_text("Garage: Acme, Tourer")
    _accessible(page)
