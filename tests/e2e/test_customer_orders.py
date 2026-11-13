"""E2E (ORD.7): the customer area "My orders" in a real browser.

A signed-in customer opens the list and the detail of an order with a shipped part: axe finds no
WCAG 2.2 AA violation, the strict CSP raises nothing, the timeline marks the current step and the
tracking link is reachable. Screenshots of the list and the detail are kept for review.
"""

import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from django.test import Client
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import UserFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.orders import services
from apps.orders.tests.integration.test_retention import place
from apps.shipping.tests.factories import CarrierFactory
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)

CTX = AuditContext(actor_label="system:e2e", source=AuditLog.Source.SYSTEM)


def _accessible(page: Page) -> None:
    violations = run_axe(page)
    assert not violations, format_violations(violations)
    assert csp_violations(page) == []


def test_a_customer_reads_the_list_and_a_shipped_order(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    user = UserFactory.create(password=secrets.token_urlsafe(16))
    CarrierFactory.create(
        code="fast-courier", tracking_url_template="https://tracking.example.com/{tracking_number}"
    )
    shipped = place(customer=user, state="awaiting_payment")
    services.transition_order(shipped, "paid", ctx=CTX)
    sub = shipped.vendor_orders.get()
    services.transition_vendor_order(sub, "accepted", ctx=CTX)
    services.transition_vendor_order(
        sub, "shipped", ctx=CTX, tracking_number="AB123", carrier="Fast Courier"
    )
    waiting = place(customer=user, state="awaiting_payment")
    client = Client()
    client.force_login(user)
    page.context.add_cookies(
        [{"name": "sessionid", "value": client.cookies["sessionid"].value, "url": live_server.url}]
    )
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    page.goto(f"{live_server.url}/account/orders/")
    expect(page.get_by_role("heading", level=1, name="My orders")).to_be_visible()
    expect(page.get_by_role("link", name=shipped.number)).to_be_visible()
    expect(page.get_by_role("link", name=waiting.number)).to_be_visible()
    _accessible(page)
    shot("list")

    page.get_by_role("link", name=shipped.number).click()
    expect(page.get_by_role("heading", level=1)).to_contain_text(shipped.number)
    expect(page.locator("li.timeline__step--current")).to_contain_text("Shipped")
    link = page.get_by_role("link", name="Track the parcel")
    expect(link).to_have_attribute("href", "https://tracking.example.com/AB123")
    _accessible(page)
    shot("detail")


def test_another_customers_order_is_not_found(page: Page, live_server: LiveServer) -> None:
    user = UserFactory.create(password=secrets.token_urlsafe(16))
    stranger_order = place(customer=UserFactory.create(), state="awaiting_payment")
    client = Client()
    client.force_login(user)
    page.context.add_cookies(
        [{"name": "sessionid", "value": client.cookies["sessionid"].value, "url": live_server.url}]
    )
    response = page.goto(f"{live_server.url}/account/orders/{stranger_order.public_id}/")
    assert response is not None
    assert response.status == 404
