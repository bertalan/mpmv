"""E2E smoke journey: the live server answers and the staff login page renders."""

from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

pytestmark = pytest.mark.django_db(transaction=True)


def test_healthz_in_browser(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page.goto(f"{live_server.url}/healthz")
    expect(page.locator("body")).to_contain_text('"status": "ok"')
    assert shot("healthz").exists()


def test_staff_login_page_renders(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page.goto(f"{live_server.url}/django-admin/login/")
    expect(page.locator("input[name=username]")).to_be_visible()
    expect(page.locator("input[name=password]")).to_be_visible()
    shot("login")
