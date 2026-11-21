"""E2E (FIT.7 2/n): the sign-in merge notice, keyboard only.

A customer signs in on a browser that holds two saved vehicles. The next page shows one non-modal
notice; *Not now* with the keyboard drops it and adds nothing; after the next sign-in, *Add*
with the keyboard copies both vehicles into the account, announces it, and clears the cookie.
axe (WCAG 2.2 AA) and the strict CSP find nothing wrong.
"""

import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from django.http import HttpResponse
from django.test import Client
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import UserFactory
from apps.fitment import garage, garage_merge
from apps.fitment.models import GarageProfile
from apps.fitment.tests.factories import FitmentNodeFactory, FitmentSchemeFactory
from apps.fitment.views import _write
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)


def _accessible(page: Page) -> None:
    violations = run_axe(page)
    assert not violations, format_violations(violations)
    assert csp_violations(page) == []


def _sign_in(page: Page, live_server: LiveServer, user: object, value: garage.Garage) -> None:
    cookie = _write(HttpResponse(), value).cookies[garage.COOKIE_NAME].value
    client = Client()
    client.force_login(user)  # type: ignore[arg-type]
    session = client.session
    session[garage_merge.SESSION_KEY] = {
        "vehicles": [v.as_dict() for v in value.vehicles],
        "active": value.active,
    }
    session.save()
    page.context.clear_cookies()
    page.context.add_cookies(
        [
            {
                "name": "sessionid",
                "value": client.cookies["sessionid"].value,
                "url": live_server.url,
            },
            {"name": garage.COOKIE_NAME, "value": cookie, "url": live_server.url},
        ]
    )


def test_a_customer_adds_the_device_garage_with_the_keyboard(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    FitmentSchemeFactory.create()
    make = FitmentNodeFactory.create(name="Acme")
    FitmentNodeFactory.create(name="Roadster", parent=make)
    FitmentNodeFactory.create(name="Tourer", parent=make)
    value = garage.Garage((garage.Vehicle("Acme", "Roadster"), garage.Vehicle("Acme", "Tourer")), 0)
    user = UserFactory.create(password=secrets.token_urlsafe(16))
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    _sign_in(page, live_server, user, value)
    page.goto(f"{live_server.url}/")
    notice = page.locator("[data-garage-merge-notice]")
    expect(notice).to_be_visible()
    expect(notice.locator("p")).to_contain_text("2")
    _accessible(page)
    shot("notice")

    notice.locator("button[value=dismiss]").focus()
    page.keyboard.press("Enter")
    expect(notice).to_have_count(0)
    assert not GarageProfile.objects.filter(user=user).exists()

    _sign_in(page, live_server, user, value)  # the next sign-in asks again
    page.goto(f"{live_server.url}/")
    notice.locator("button[value=add]").focus()
    page.keyboard.press("Enter")
    expect(page.locator(".site-messages [role=status]")).to_contain_text("2")
    expect(notice).to_have_count(0)
    assert GarageProfile.objects.filter(user=user).count() == 2
    assert all(c["name"] != garage.COOKIE_NAME for c in page.context.cookies())
    _accessible(page)
    shot("added")
