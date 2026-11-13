"""E2E (CMP.6 2/2): the online withdrawal function in a real browser, and the pre-contractual
information that says where it is.

Done when: the withdrawal button ("withdraw from contract here", IT "recedere dal contratto
qui") is reachable, a second step labelled "confirm withdrawal" records the statement, and the
consumer gets an acknowledgement with the date and time (and the same by email).
"""

import re
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.compliance.models import WithdrawalStatement
from apps.core.models import OutboxEmail
from apps.orders.tests.integration.test_services import line_input, pay, place, vendor_input
from apps.vendors.tests.factories import VendorFactory
from tests.e2e.test_cms_pages import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)


def test_a_consumer_withdraws_in_two_steps_and_gets_an_acknowledgement(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    vendor = VendorFactory.create()
    order = place(
        language="en",
        vendor_orders=[vendor_input(vendor, lines=[line_input(title="Ceramic cup")])],
    )
    pay(order)
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    page.goto(f"{live_server.url}/consumer-information/")
    expect(page.get_by_role("heading", level=1)).to_have_text("Consumer information")
    shot("information")
    page.get_by_role("link", name="withdraw from contract here").click()

    expect(page.get_by_role("heading", level=1)).to_have_text("Withdraw from contract here")
    page.get_by_label("Order number").fill(order.number)
    page.get_by_label("Email address of the order").fill(order.email)
    shot("step-one")
    page.get_by_role("button", name="Continue").click()

    expect(page.get_by_text("Ceramic cup")).to_be_visible()
    page.get_by_role("checkbox").check()
    shot("step-two")
    page.get_by_role("button", name="Confirm withdrawal").click()

    expect(page).to_have_url(re.compile(r"/withdrawal/done/$"))
    expect(page.get_by_role("heading", level=1)).to_have_text("Withdrawal received")
    statement = WithdrawalStatement.objects.get()
    expect(page.get_by_text(str(statement.public_id))).to_be_visible()
    shot("acknowledgement")
    assert (
        OutboxEmail.objects.filter(template_key="compliance.withdrawal_acknowledgement").count()
        == 1
    )
    assert csp_violations(page) == [], "\n".join(csp_violations(page))


def test_the_italian_labels_are_the_decree_s_words(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page.goto(f"{live_server.url}/it/withdrawal/")
    expect(page.get_by_role("heading", level=1)).to_have_text("Recedere dal contratto qui")
    shot("it-step-one")
