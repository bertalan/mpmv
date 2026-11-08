"""E2E (THM.4): the staff-only styleguide page renders every component (spec 14 §14.9).

A verified staff member opens `/_styleguide/`: every section is shown, the page raises no CSP
violation, the keyboard focus stays visible, and full-page screenshots are saved at a desktop
and a phone width for review (build/e2e/screenshots).
"""

import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import totp_token
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations
from tests.e2e.login import admin_login, verify_with_totp
from tests.e2e.test_base_theme_keyboard import FOCUSED, assert_visible_focus

pytestmark = pytest.mark.django_db(transaction=True)

SECTIONS = ("Buttons", "Text fields", "Choices", "Error summary")


def open_styleguide(page: Page, live_server: LiveServer) -> None:
    password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    staff = UserFactory.create(email="staff@example.com", password=password, superuser=True)
    device = TOTPDeviceFactory.create(user=staff)
    admin_login(page, live_server.url, "staff@example.com", password)
    verify_with_totp(page, totp_token(device))
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    response = page.goto(f"{live_server.url}/_styleguide/")
    assert response is not None
    assert response.status == 200
    page.wait_for_load_state("load")


def test_staff_see_every_component_without_a_csp_violation(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page.set_viewport_size({"width": 1280, "height": 900})
    open_styleguide(page, live_server)
    expect(page.locator("html")).to_have_attribute("data-theme", "base")
    for section in SECTIONS:
        expect(page.get_by_role("heading", name=section, exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Close")).to_be_visible()
    expect(page.locator(".error-summary a").first).to_be_visible()
    shot("desktop")

    page.set_viewport_size({"width": 375, "height": 800})
    page.wait_for_timeout(100)  # the layout settles after the resize
    shot("phone")
    assert csp_violations(page) == [], "\n".join(csp_violations(page))


def test_the_keyboard_focus_is_visible_on_every_component(
    page: Page, live_server: LiveServer
) -> None:
    page.set_viewport_size({"width": 1280, "height": 900})
    open_styleguide(page, live_server)
    stops: list[dict[str, Any]] = []
    for _ in range(60):
        page.keyboard.press("Tab")
        focused = page.evaluate(FOCUSED)
        if focused is not None:
            stops.append(focused)
    assert len(stops) > 30
    assert_visible_focus(stops)


def test_an_error_summary_link_leads_to_its_field(page: Page, live_server: LiveServer) -> None:
    page.set_viewport_size({"width": 1280, "height": 900})
    open_styleguide(page, live_server)
    link = page.locator(".error-summary a").first
    target = (link.get_attribute("href") or "").lstrip("#")
    assert target
    link.click()
    control = page.locator(f"#{target}")
    expect(control).to_be_in_viewport()
    expect(control).to_have_attribute("aria-invalid", "true")
