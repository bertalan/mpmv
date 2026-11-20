"""E2E (FIT.5 2/3): the "My garage" page in a real browser.

A signed-in customer opens the page, makes a second vehicle active, walks through the delete
confirmation, and axe (WCAG 2.2 AA) and the strict CSP find nothing wrong. The garage is in no URL.
"""

import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from django.test import Client
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import UserFactory
from apps.fitment.models import GarageProfile
from apps.fitment.tests.factories import (
    FitmentLevelFactory,
    FitmentNodeFactory,
    FitmentSchemeFactory,
    GarageProfileFactory,
)
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)


def _accessible(page: Page) -> None:
    violations = run_axe(page)
    assert not violations, format_violations(violations)
    assert csp_violations(page) == []


def test_a_customer_manages_the_garage(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    # A transactional test flushes the scheme row the migration seeds (it is order-dependent).
    FitmentSchemeFactory.create()
    for position, code in enumerate(["make", "model"], start=1):
        FitmentLevelFactory.create(position=position, code=code)
    user = UserFactory.create(password=secrets.token_urlsafe(16))
    make = FitmentNodeFactory.create(name="Make One")
    GarageProfileFactory.create(
        user=user, name="Weekend bike", is_active=True, node=FitmentNodeFactory.create(parent=make)
    )
    GarageProfileFactory.create(
        user=user, name="Old car", node=FitmentNodeFactory.create(parent=make)
    )
    client = Client()
    client.force_login(user)
    page.context.add_cookies(
        [{"name": "sessionid", "value": client.cookies["sessionid"].value, "url": live_server.url}]
    )
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    page.goto(f"{live_server.url}/account/garage/")
    expect(page.get_by_role("heading", level=1, name="My garage")).to_be_visible()
    expect(page.locator("[data-garage-active]")).to_contain_text("Weekend bike")
    _accessible(page)
    shot("list")

    page.locator("[data-garage-profile]", has_text="Old car").get_by_role(
        "button", name="Make active"
    ).click()
    expect(page.locator("[data-garage-active]")).to_contain_text("Old car")
    assert page.url == f"{live_server.url}/account/garage/"

    page.locator("[data-garage-profile]", has_text="Weekend bike").get_by_role(
        "link", name="Delete"
    ).click()
    expect(page.get_by_role("heading", level=1, name="Delete this vehicle?")).to_be_visible()
    _accessible(page)
    shot("delete")
    page.get_by_role("button", name="Yes, delete it").click()
    expect(page.get_by_role("heading", level=1, name="My garage")).to_be_visible()
    assert GarageProfile.objects.filter(user=user).count() == 1
