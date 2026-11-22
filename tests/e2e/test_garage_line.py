"""E2E (FIT.7 4/n): the dismissible "Add your garage" line, keyboard only.

A visitor with no garage sees the line above a list (no pop-up, focus untouched); the keyboard
reaches *Dismiss*, which returns to the same list with the line gone; reload and the language
switch keep it so. axe (WCAG 2.2 AA) and the strict CSP find nothing wrong.
"""

from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.fitment.tests.factories import FitmentNodeFactory, FitmentSchemeFactory
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)


def _accessible(page: Page) -> None:
    violations = run_axe(page)
    assert not violations, format_violations(violations)
    assert csp_violations(page) == []


def test_a_visitor_dismisses_the_line_with_the_keyboard(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    FitmentSchemeFactory.create()
    make = FitmentNodeFactory.create(name="Acme")
    FitmentNodeFactory.create(name="Roadster", parent=make)
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    page.goto(f"{live_server.url}/?s=zzz")
    line = page.locator("[data-garage-line]")
    expect(line).to_have_count(1)
    focused = page.evaluate("document.activeElement && document.activeElement.tagName")
    assert focused in (None, "BODY")  # the line does not steal focus
    _accessible(page)
    shot("line")

    line.locator("form button").focus()
    page.keyboard.press("Enter")  # Dismiss
    expect(page.locator("[data-garage-line]")).to_have_count(0)
    assert "s=zzz" in page.url  # back on the same list
    shot("dismissed")

    page.reload()
    expect(page.locator("[data-garage-line]")).to_have_count(0)
    page.go_back()
    page.go_forward()
    expect(page.locator("[data-garage-line]")).to_have_count(0)
    _accessible(page)
