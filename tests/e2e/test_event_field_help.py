"""E2E (ARC.31 slice 1): the Wagtail editor of an event shows a help text next to its fields.

Done when: staff open the EventPage editor and see the help text of a field right beside it.
A screenshot is kept for review (`build/e2e/screenshots`).
"""

import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import totp_token
from apps.events.tests.factories import EventPageFactory
from tests.e2e.login import verify_with_totp

pytestmark = pytest.mark.django_db(transaction=True)

SUMMARY_HELP = "One or two sentences that tell what the event is, up to 300 characters."


def test_the_event_editor_shows_the_help_text_next_to_a_field(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    user = UserFactory.create(email="editor@example.com", password=password, superuser=True)
    device = TOTPDeviceFactory.create(user=user)
    event = EventPageFactory.create(title="Autumn fair")
    page.goto(f"{live_server.url}/admin/login/")
    page.locator("input[name=username]").fill("editor@example.com")
    page.locator("input[name=password]").fill(password)
    page.get_by_role("button", name="Sign in").click()
    verify_with_totp(page, totp_token(device))

    page.goto(f"{live_server.url}/admin/pages/{event.pk}/edit/")
    field = page.locator("[data-field]").filter(has=page.locator("#id_summary"))
    expect(field).to_be_visible()
    help_text = field.get_by_text(SUMMARY_HELP, exact=False)
    expect(help_text).to_be_visible()
    help_text.scroll_into_view_if_needed()
    shot("event-field-help")
