"""E2E (ORD.20 part 3): with the keyboard only, a vendor removes an unavailable line, reads the
preview, confirms the refund with a passkey, and the customer sees version 2 and the refund.

Chromium's WebAuthn virtual authenticator stands in for the platform authenticator, so the
"Do it once" ceremony of the passkey prompt runs for real against the server. Stripe is the
double behind our own provider. Every page passes axe and the strict CSP.
"""

import re
import secrets
from collections.abc import Callable, Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from playwright.sync_api import CDPSession, Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import UserFactory
from apps.ledger.models import Refund
from apps.ledger.tests.integration.test_amendment_refunds import (  # noqa: F401 (fixtures)
    _setup,
    place_seed,
    stripe,
)
from apps.orders.models import AmendmentState, OrderAmendment
from apps.payments.tests.fake_stripe import FakeStripeClient
from apps.vendors.tests.factories import VendorMemberFactory
from tests.e2e.csp import RECORD_CSP_VIOLATIONS
from tests.e2e.test_manager_shell import _accessible, _login

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def virtual_authenticator(page: Page) -> Iterator[CDPSession]:
    session = page.context.new_cdp_session(page)
    session.send("WebAuthn.enable")
    session.send(
        "WebAuthn.addVirtualAuthenticator",
        {
            "options": {
                "protocol": "ctap2",
                "transport": "internal",
                "hasResidentKey": True,
                "hasUserVerification": True,
                "isUserVerified": True,
                "automaticPresenceSimulation": True,
            }
        },
    )
    yield session
    session.detach()


def _press(page: Page, selector: str, key: str = "Space") -> None:
    page.locator(selector).focus()
    page.keyboard.press(key)


def test_a_vendor_removes_an_unavailable_line_and_the_customer_sees_v2(
    page: Page,
    live_server: LiveServer,
    virtual_authenticator: CDPSession,
    stripe: FakeStripeClient,  # noqa: F811
    shot: Callable[[str], Path],
) -> None:
    customer = UserFactory.create(email="buyer@example.com")
    sub = place_seed(stripe, customer=customer)
    password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    owner = UserFactory.create(email="seller@example.com", password=password)
    VendorMemberFactory.create(vendor=sub.vendor, user=owner, role="owner")
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    # Sign in; the first sign-in enrols a passkey (the virtual authenticator answers).
    _login(page, live_server.url, "seller@example.com", password)
    expect(page).to_have_url(re.compile(r"/account/mfa/setup/"))
    expect(page.locator("form[data-passkey-ready]")).to_have_count(1)
    page.get_by_role("button", name="Register a passkey").focus()
    page.keyboard.press("Enter")
    expect(page.locator("ol.recovery-codes li")).to_have_count(10)

    page.goto(f"{live_server.url}/manager/shop/orders/{sub.public_id}/")
    page.get_by_role("link", name="Change this order").focus()
    page.keyboard.press("Enter")
    expect(page.get_by_role("heading", name=re.compile("Change order"))).to_be_visible()
    _accessible(page)
    shot("amend-form")

    key = str(sub.lines.get(title="B").public_id)
    _press(page, f"#id_line_{key}_remove")
    _press(page, f"#id_line_{key}_unavailable")
    page.locator("#id_reason").focus()
    page.keyboard.type("B is out of stock at the supplier")
    page.get_by_role("button", name="Compute the change").focus()
    page.keyboard.press("Enter")

    preview = page.locator("[data-amendment-preview]")
    expect(preview.locator("[data-amendment-money]")).to_have_text(
        "36.60 EUR refunded to the customer"
    )
    expect(preview.locator("[data-amendment-kind]")).to_contain_text("Minor change")
    _accessible(page)
    shot("amend-preview")

    page.get_by_role("button", name="Apply the change").focus()
    page.keyboard.press("Enter")
    expect(page.get_by_role("heading", name="Confirm with your passkey")).to_be_visible()
    expect(page.locator("form[data-passkey-ready]")).to_have_count(1)
    _accessible(page)
    shot("passkey-prompt")
    page.get_by_role("button", name="Confirm with my passkey").focus()
    page.keyboard.press("Enter")

    expect(page.get_by_text(re.compile("36.60 EUR is refunded to the customer"))).to_be_visible()
    expect(page.locator("[data-version]").first).to_have_text("2")
    _accessible(page)
    shot("amend-applied")
    amendment = OrderAmendment.objects.get(vendor_order=sub)
    assert amendment.state == AmendmentState.APPLIED
    assert Refund.objects.get(amendment=amendment).refunded_gross == Decimal("36.60")

    # The customer's order page: version 2, what changed, the refund and the offer.
    page.context.clear_cookies()
    from django.test import Client

    client = Client()
    client.force_login(customer)
    page.context.add_cookies(
        [{"name": "sessionid", "value": client.cookies["sessionid"].value, "url": live_server.url}]
    )
    page.goto(f"{live_server.url}/account/orders/{sub.order.public_id}/")
    versions = page.locator("[data-versions]")
    expect(versions.locator('[data-version="2"]')).to_contain_text("B removed as unavailable")
    expect(versions.locator('[data-version="2"]')).to_contain_text("36.60 EUR refunded")
    expect(page.locator("[data-cancel-rest-offer]")).to_be_visible()
    _accessible(page)
    shot("customer-v2")
