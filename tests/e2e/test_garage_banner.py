"""E2E (FIT.7 3/n): the garage banner on a list, keyboard only.

A visitor with an active vehicle sees the "Garage" region above a list's results; *Show all* with
the keyboard resets to generic, returns to the same list and announces it; the banner is gone and
reload keeps it so. axe (WCAG 2.2 AA) and the strict CSP find nothing wrong.
"""

from collections.abc import Callable
from pathlib import Path

import pytest
from django.http import HttpResponse
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.fitment import garage
from apps.fitment.tests.factories import FitmentNodeFactory, FitmentSchemeFactory
from apps.fitment.views import _write
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)


def _accessible(page: Page) -> None:
    violations = run_axe(page)
    assert not violations, format_violations(violations)
    assert csp_violations(page) == []


def test_a_visitor_shows_all_from_the_banner_with_the_keyboard(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    FitmentSchemeFactory.create()
    make = FitmentNodeFactory.create(name="Acme")
    FitmentNodeFactory.create(name="Roadster", parent=make)
    value = garage.Garage((garage.Vehicle("Acme", "Roadster"),), 0)
    cookie = _write(HttpResponse(), value).cookies[garage.COOKIE_NAME].value
    page.context.add_cookies(
        [{"name": garage.COOKIE_NAME, "value": cookie, "url": live_server.url}]
    )
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    page.goto(f"{live_server.url}/?s=zzz")
    banner = page.locator("[data-garage-banner]")
    expect(banner).to_contain_text("Acme, Roadster")
    _accessible(page)
    shot("banner")

    banner.locator("form button").focus()
    page.keyboard.press("Enter")  # Show all
    expect(page.locator("[data-garage-banner]")).to_have_count(0)
    expect(page.locator("[data-garage-menu] summary")).to_have_text("Garage: generic")
    assert "s=zzz" in page.url  # back on the same list
    shot("show-all")

    page.reload()
    expect(page.locator("[data-garage-banner]")).to_have_count(0)
    _accessible(page)
