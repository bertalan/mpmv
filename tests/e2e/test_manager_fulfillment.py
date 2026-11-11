"""E2E (ORD.6): a vendor accepts, ships with tracking and marks delivered from the Manager.

Also checks that another vendor's order is not in the list and that every page passes axe and the
strict CSP. Screenshots are kept for review.
"""

import re
import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import totp_token
from apps.orders.tests.factories import paid_vendor_order
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory
from tests.e2e.csp import RECORD_CSP_VIOLATIONS
from tests.e2e.login import verify_with_totp
from tests.e2e.test_manager_shell import _accessible, _login

pytestmark = pytest.mark.django_db(transaction=True)


def test_the_vendor_fulfils_an_order_end_to_end(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    password = secrets.token_urlsafe(16)
    user = UserFactory.create(email="seller@example.com", password=password)
    device = TOTPDeviceFactory.create(user=user)
    mine = VendorFactory.create(display_name="Shop North")
    VendorMemberFactory.create(vendor=mine, user=user)
    sub = paid_vendor_order(mine, order__customer_name="Mario Rossi")
    theirs = paid_vendor_order(VendorFactory.create(display_name="Shop South"))
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    _login(page, live_server.url, "seller@example.com", password)
    verify_with_totp(page, totp_token(device))
    page.goto(f"{live_server.url}/manager/shop/orders/")
    expect(page.get_by_role("heading", name="Orders")).to_be_visible()
    expect(page.locator("main")).to_contain_text(sub.order.number)
    expect(page.locator("main")).not_to_contain_text(theirs.order.number)
    _accessible(page)
    shot("orders-list")

    page.get_by_role("link", name=sub.order.number).click()
    expect(page.locator("[data-order-state]")).to_have_text("Pending")
    _accessible(page)
    shot("order-pending")
    page.get_by_role("button", name="Accept the order").click()
    expect(page.locator("[data-order-state]")).to_have_text("Accepted")

    page.get_by_role("button", name="Mark as shipped").click()  # no tracking number yet
    expect(page.get_by_role("region", name="There is a problem")).to_be_visible()
    _accessible(page)
    shot("order-ship-error")
    page.get_by_label("Tracking number").fill("TRK-12345")
    page.get_by_label("Carrier").fill("Poste")
    page.get_by_role("button", name="Mark as shipped").click()
    expect(page.locator("[data-order-state]")).to_have_text("Shipped")
    expect(page.locator("main")).to_contain_text("TRK-12345")
    _accessible(page)
    shot("order-shipped")

    page.get_by_role("button", name="Mark as delivered").click()
    expect(page.locator("[data-order-state]")).to_have_text("Delivered")
    expect(page).to_have_url(re.compile(r"/manager/shop/orders/[0-9a-f-]+/$"))
    _accessible(page)
    shot("order-delivered")
