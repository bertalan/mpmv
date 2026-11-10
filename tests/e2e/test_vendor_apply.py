"""E2E (VEN.22): a visitor applies from the public "Sell with us" form, confirms the email through
the emailed link and ends up as an inactive-then-active applicant; the form passes axe (WCAG 2.2
AA) under the strict CSP."""

import json
import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.models import User
from apps.core.models import OutboxEmail
from apps.vendors import engine
from apps.vendors.models import Vendor
from tests.e2e.axe import format_violations, run_axe

pytestmark = pytest.mark.django_db(transaction=True)


def emailed_link_path() -> str:
    email = OutboxEmail.objects.filter(template_key="vendors.application_confirm").first()
    assert email is not None
    url = str(json.loads(email.context)["action_url"])
    return "/" + url.split("/", 3)[3]


def test_a_visitor_applies_and_confirms_the_email(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page.goto(f"{live_server.url}/vendor/apply/")
    expect(page.get_by_role("heading", level=1)).to_have_text("Apply to sell with us")
    violations = run_axe(page)
    assert violations == [], format_violations(violations)
    shot("apply-form")

    page.get_by_label("Your full name").fill("Ada Example")
    page.get_by_label("Email address").fill("ada@example.com")
    page.get_by_label("Name of your business or shop").fill("Ada Workshop")
    page.get_by_label("Country where your business is established").select_option("IT")
    page.get_by_role("button", name="Send my application").click()
    expect(page.get_by_role("heading", name="Check your email")).to_be_visible()
    shot("apply-sent")

    user = User.objects.get(email="ada@example.com")
    assert user.is_active is False  # nothing works before the email is confirmed
    assert Vendor.objects.get(contact_email="ada@example.com").status == engine.APPLICANT

    page.goto(f"{live_server.url}{emailed_link_path()}")
    page.get_by_role("button", name="Continue").click()
    expect(page.get_by_role("heading", name="Confirm your email address")).to_be_visible()
    violations = run_axe(page)
    assert violations == [], format_violations(violations)
    shot("apply-confirm")
    assert User.objects.get(email="ada@example.com").has_usable_password() is False
    page.get_by_label("Choose a password").fill(secrets.token_urlsafe(18))
    page.get_by_role("button", name="Confirm my email").click()
    expect(page.get_by_role("heading", name="Your email is confirmed")).to_be_visible()
    shot("apply-confirmed")

    user.refresh_from_db()
    assert user.is_active is True
    assert Vendor.objects.get(contact_email="ada@example.com").status == engine.APPLICANT


def test_the_address_owner_cancels_with_the_this_was_not_me_link(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page.goto(f"{live_server.url}/vendor/apply/")
    page.get_by_label("Your full name").fill("Ada Example")
    page.get_by_label("Email address").fill("ada@example.com")
    page.get_by_label("Name of your business or shop").fill("Ada Workshop")
    page.get_by_label("Country where your business is established").select_option("IT")
    page.get_by_role("button", name="Send my application").click()
    expect(page.get_by_role("heading", name="Check your email")).to_be_visible()

    email = OutboxEmail.objects.filter(template_key="vendors.application_confirm").first()
    assert email is not None
    cancel_url = str(json.loads(email.context)["cancel_url"])
    page.goto(f"{live_server.url}/{cancel_url.split('/', 3)[3]}")
    page.get_by_role("button", name="Continue").click()
    expect(page.get_by_role("heading", name="Cancel this application")).to_be_visible()
    assert User.objects.filter(email="ada@example.com").exists()  # a GET deletes nothing
    shot("apply-cancel")
    page.get_by_role("button", name="Cancel the application, this was not me").click()
    expect(page.get_by_role("heading", name="Nothing is waiting")).to_be_visible()
    shot("apply-cancelled")
    assert not User.objects.filter(email="ada@example.com").exists()
    assert not Vendor.objects.filter(contact_email="ada@example.com").exists()


def test_a_form_with_errors_lists_them_in_a_summary(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page.goto(f"{live_server.url}/vendor/apply/")
    page.get_by_label("Email address").fill("not-an-email")
    page.get_by_role("button", name="Send my application").click()
    expect(page.get_by_role("region", name="There is a problem")).to_be_visible()
    violations = run_axe(page)
    assert violations == [], format_violations(violations)
    shot("apply-errors")
