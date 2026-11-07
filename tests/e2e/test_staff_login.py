"""E2E: a staff member logs into the admin with email and password (CORE.1)."""

import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import UserFactory

pytestmark = pytest.mark.django_db(transaction=True)


def test_staff_logs_in_with_email(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    UserFactory.create(email="staff@example.com", password=password, superuser=True)
    page.goto(f"{live_server.url}/django-admin/login/")
    expect(page.get_by_label("Email")).to_be_visible()
    page.get_by_label("Email").fill("staff@example.com")
    page.get_by_label("Password").fill(password)
    shot("filled")
    page.get_by_role("button", name="Log in").click()
    expect(
        page.get_by_role("link", name="Log out").or_(page.get_by_role("button", name="Log out"))
    ).to_be_visible()
    shot("logged-in")
