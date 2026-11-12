"""E2E (VEN.9): the vendor opens the money pages of the Manager: balance, transactions, payouts,
statement. Axe and the strict CSP are checked on every page; screenshots are kept for review."""

import secrets
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import totp_token
from apps.core.audit import AuditContext
from apps.ledger.services import record_entry
from apps.ledger.tests.factories import _open_operator
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory
from tests.e2e.csp import RECORD_CSP_VIOLATIONS
from tests.e2e.login import verify_with_totp
from tests.e2e.test_manager_shell import _accessible, _login

pytestmark = pytest.mark.django_db(transaction=True)


def test_the_vendor_reads_its_money_pages(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    password = secrets.token_urlsafe(16)
    user = UserFactory.create(email="seller@example.com", password=password)
    device = TOTPDeviceFactory.create(user=user)
    mine = VendorFactory.create(display_name="Shop North")
    VendorMemberFactory.create(vendor=mine, user=user)
    theirs = VendorFactory.create(display_name="Shop South")
    ctx = AuditContext(actor_label="system:test", source="system")
    for vendor, kind, amount, memo in (
        (mine, "sale", "120.00", "Order north"),
        (mine, "commission", "-12.00", ""),
        (mine, "payout", "-50.00", "Payout June"),
        (theirs, "sale", "999.00", "Order south"),
    ):
        record_entry(
            vendor,
            kind=kind,
            amount=Decimal(amount),
            idempotency_key=secrets.token_urlsafe(8),
            ctx=ctx,
            memo=memo,
            platform_entity=_open_operator(),
        )
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    _login(page, live_server.url, "seller@example.com", password)
    verify_with_totp(page, totp_token(device))
    page.goto(f"{live_server.url}/manager/shop/money/")
    expect(page.get_by_role("heading", name="Money")).to_be_visible()
    expect(page.locator("[data-balance]")).to_contain_text("58")
    expect(page.locator("main")).to_contain_text("Payout June")
    expect(page.locator("main")).not_to_contain_text("Order south")
    _accessible(page)
    shot("money-dashboard")

    page.get_by_role("link", name="Transactions").first.click()
    expect(page.get_by_role("heading", name="Transactions")).to_be_visible()
    expect(page.locator("main")).to_contain_text("Order north")
    _accessible(page)
    shot("money-transactions")

    page.get_by_role("link", name="Statement").first.click()
    expect(page.locator("[data-statement=closing]")).to_contain_text("58")
    _accessible(page)
    shot("money-statement")
