"""E2E: repeated failed logins lock the account with a generic error (CORE.5)."""

import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.services import ACCOUNT_LOCKOUT
from apps.accounts.tests.factories import UserFactory

pytestmark = pytest.mark.django_db(transaction=True)

GENERIC_ERROR = "Please enter the correct email and password for a staff account."


def _submit(page: Page, url: str, email: str, password: str) -> None:
    page.goto(url)
    page.get_by_label("Email").fill(email)
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Log in").click()


def test_staff_is_locked_out_after_repeated_failures(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    UserFactory.create(email="staff@example.com", password=password, superuser=True)
    url = f"{live_server.url}/django-admin/login/"

    for _ in range(ACCOUNT_LOCKOUT.free_attempts):
        _submit(page, url, "staff@example.com", secrets.token_urlsafe(16))
        expect(page.get_by_text(GENERIC_ERROR)).to_be_visible()
    shot("failed-attempts")

    _submit(page, url, "staff@example.com", password)
    expect(page.get_by_text(GENERIC_ERROR)).to_be_visible()  # same message: nothing leaks
    expect(page.get_by_role("button", name="Log in")).to_be_visible()
    shot("locked-out")
