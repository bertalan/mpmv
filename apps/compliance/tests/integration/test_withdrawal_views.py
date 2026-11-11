"""The public withdrawal pages (CMP.6 2/2): identify, choose, confirm, acknowledgement; and the
pre-contractual information page."""

import datetime as dt
import re
from collections.abc import Iterator
from typing import Any

import pytest
from django.core import signing
from django.test import Client
from django.urls import reverse
from django.utils import timezone, translation

from apps.accounts.captcha import TOKEN_FIELD
from apps.accounts.tests.fakes import FakeCaptchaVerifier
from apps.compliance import withdrawal
from apps.compliance.models import WithdrawalStatement
from apps.core.models import OutboxEmail, Throttle
from apps.orders.models import Order, VendorOrder
from apps.orders.tests.integration.test_services import line_input, pay, place, vendor_input
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

IP = "203.0.113.9"


@pytest.fixture(autouse=True)
def _english() -> Iterator[None]:
    translation.activate("en")
    yield
    translation.activate("en")


@pytest.fixture(autouse=True)
def _reset_captcha() -> Iterator[None]:
    FakeCaptchaVerifier.reset()
    yield
    FakeCaptchaVerifier.reset()


def paid_order(**overrides: object) -> Order:
    a = vendor_input(VendorFactory.create(), lines=[line_input(title="Cup from A")])
    b = vendor_input(VendorFactory.create(), lines=[line_input(title="Bowl from B", sku="B-1")])
    order = place(vendor_orders=[a, b], **overrides)
    pay(order)
    order.refresh_from_db()
    return order


def identify(client: Client, order: Order, **extra: str) -> Any:
    data = {"order_number": order.number, "email": order.email, **extra}
    return client.post(reverse("withdrawal"), data, REMOTE_ADDR=IP)


def token_of(html: str) -> str:
    match = re.search(r'name="token" value="([^"]+)"', html)
    assert match, "the choice page carries the signed token"
    return match.group(1)


# --- The entry page and the labels


def test_the_start_page_is_public_and_uses_the_mandated_label_in_english(client: Client) -> None:
    response = client.get(reverse("withdrawal"))
    assert response.status_code == 200
    assert "withdraw from contract here" in response.content.decode().lower()
    assert response["Cache-Control"] == "no-store"


def test_the_start_page_uses_the_italian_labels(client: Client) -> None:
    response = client.get("/it/withdrawal/")
    assert response.status_code == 200
    assert "recedere dal contratto qui" in response.content.decode().lower()


def test_the_link_tag_renders_the_label_for_any_page(client: Client) -> None:
    from django.template import Context, Template

    html = Template("{% load compliance_tags %}{% withdrawal_link %}").render(Context({}))
    assert reverse("withdrawal") in html
    assert "withdraw from contract here" in html.lower()


# --- Step 1: identify the contract


def test_the_right_number_and_email_show_each_vendor_order_to_choose(client: Client) -> None:
    order = paid_order()
    response = identify(client, order)
    assert response.status_code == 200
    html = response.content.decode()
    assert "confirm withdrawal" in html.lower()
    for sub in order.vendor_orders.all():
        assert str(sub.public_id) in html
    assert "Cup from A" in html
    assert "Bowl from B" in html
    assert not WithdrawalStatement.objects.exists()  # nothing is recorded before the confirmation


def test_a_wrong_email_gets_the_same_answer_as_a_wrong_number(client: Client) -> None:
    order = paid_order()
    wrong_email = client.post(
        reverse("withdrawal"), {"order_number": order.number, "email": "x@example.com"}
    )
    wrong_number = client.post(
        reverse("withdrawal"), {"order_number": "2999-999999", "email": order.email}
    )
    assert wrong_email.status_code == wrong_number.status_code == 200
    assert wrong_email.content.decode().count("We could not find") == 1
    assert wrong_number.content.decode().count("We could not find") == 1


def test_the_choice_page_is_not_cached_and_hides_the_email(client: Client) -> None:
    order = paid_order()
    response = identify(client, order)
    assert response["Cache-Control"] == "no-store"
    assert order.email not in response.content.decode()


def test_a_closed_vendor_order_is_shown_as_closed_and_cannot_be_chosen(client: Client) -> None:
    order = paid_order()
    old = timezone.now() - dt.timedelta(days=40)
    first = order.vendor_orders.order_by("id")[0]
    VendorOrder.objects.filter(pk=first.pk).update(delivered_at=old)
    html = identify(client, order).content.decode()
    assert f'value="{first.public_id}"' not in html  # no checkbox for it
    second = order.vendor_orders.order_by("id")[1]
    assert f'value="{second.public_id}"' in html
    assert "period has ended" in html.lower()


# --- Step 2: confirm


def confirm(client: Client, html: str, *ids: object) -> Any:
    return client.post(
        reverse("withdrawal_confirm"),
        {"token": token_of(html), "vendor_orders": [str(i) for i in ids]},
        REMOTE_ADDR=IP,
    )


def test_confirming_records_the_statement_and_shows_the_acknowledgement(client: Client) -> None:
    order = paid_order()
    html = identify(client, order).content.decode()
    chosen = order.vendor_orders.order_by("id")[1]
    response = confirm(client, html, chosen.public_id)
    assert response.status_code == 302
    statement = WithdrawalStatement.objects.get()
    assert statement.vendor_order == chosen
    done = client.get(response["Location"])
    page = done.content.decode()
    assert str(statement.public_id) in page
    assert order.number in page
    assert OutboxEmail.objects.filter(template_key=withdrawal.ACK_TEMPLATE).count() == 1
    assert done["Cache-Control"] == "no-store"


def test_the_done_page_needs_a_confirmation_first(client: Client) -> None:
    assert client.get(reverse("withdrawal_done")).status_code == 404


def test_confirming_with_nothing_chosen_asks_again(client: Client) -> None:
    order = paid_order()
    html = identify(client, order).content.decode()
    response = confirm(client, html)
    assert response.status_code == 200
    assert "Choose at least one" in response.content.decode()
    assert not WithdrawalStatement.objects.exists()


def test_a_forged_or_expired_token_is_refused(client: Client) -> None:
    order = paid_order()
    sub = order.vendor_orders.first()
    assert sub is not None
    forged = client.post(
        reverse("withdrawal_confirm"),
        {"token": "not-a-token", "vendor_orders": [str(sub.public_id)]},
    )
    assert forged.status_code == 302
    assert forged["Location"] == reverse("withdrawal")
    stale = signing.dumps({"order": str(order.public_id)}, salt="compliance.withdrawal")
    assert withdrawal.read_token(stale, max_age=-1) is None
    assert not WithdrawalStatement.objects.exists()


def test_a_token_for_one_order_cannot_withdraw_another_orders_vendor_order(client: Client) -> None:
    mine, other = paid_order(), paid_order()
    html = identify(client, mine).content.decode()
    foreign = other.vendor_orders.first()
    assert foreign is not None
    response = confirm(client, html, foreign.public_id)
    assert response.status_code == 200
    assert not WithdrawalStatement.objects.exists()


def test_a_token_works_once(client: Client) -> None:
    order = paid_order()
    html = identify(client, order).content.decode()
    target = order.vendor_orders.order_by("id")[0].public_id
    assert confirm(client, html, target).status_code == 302
    again = confirm(client, html, target)
    assert again.status_code == 302
    assert again["Location"] == reverse("withdrawal")  # the token is spent: identify again
    assert WithdrawalStatement.objects.count() == 1


def test_a_token_is_bound_to_the_order_email(client: Client) -> None:
    order = paid_order()
    token = withdrawal.make_token(order)
    assert withdrawal.read_token(token) == order
    other = signing.dumps(
        {"order": str(order.public_id), "email": "not-the-hash", "used": 0},
        salt=withdrawal.TOKEN_SALT,
    )
    assert withdrawal.read_token(other) is None  # the order's email is not the one it names


def test_an_error_page_keeps_the_original_token(client: Client) -> None:
    order = paid_order()
    html = identify(client, order).content.decode()
    original = token_of(html)
    response = confirm(client, html)  # nothing chosen
    assert token_of(response.content.decode()) == original  # the 30 minutes are not extended


# --- Protection


def test_the_captcha_is_checked_on_the_identification(client: Client, settings: Any) -> None:
    settings.CAPTCHA_ENABLED = True
    settings.CAPTCHA_VERIFIER = "apps.accounts.tests.fakes.FakeCaptchaVerifier"
    order = paid_order()
    refused = identify(client, order)
    assert "confirm withdrawal" not in refused.content.decode().lower()
    accepted = identify(client, order, **{TOKEN_FIELD: FakeCaptchaVerifier.VALID_TOKEN})
    assert "confirm withdrawal" in accepted.content.decode().lower()


def test_lookups_are_rate_limited_per_address(client: Client) -> None:
    for n in range(withdrawal.LOOKUP_RATE_LIMIT):
        client.post(
            reverse("withdrawal"),
            {"order_number": f"2999-{n:06d}", "email": "a@example.com"},
            REMOTE_ADDR=IP,
        )
    blocked = client.post(
        reverse("withdrawal"),
        {"order_number": "2999-000099", "email": "a@example.com"},
        REMOTE_ADDR=IP,
    )
    assert blocked.status_code == 429
    assert int(blocked["Retry-After"]) > 0
    assert Throttle.objects.exists()


def test_lookups_are_rate_limited_per_order_from_many_addresses(client: Client) -> None:
    order = paid_order()
    for n in range(withdrawal.ORDER_LOOKUP_RATE_LIMIT):
        client.post(
            reverse("withdrawal"),
            {"order_number": order.number, "email": "guess@example.com"},
            REMOTE_ADDR=f"198.51.100.{n + 1}",
        )
    limited = client.post(
        reverse("withdrawal"),
        {"order_number": order.number, "email": order.email},  # even the right pair
        REMOTE_ADDR="198.51.100.200",
    )
    assert limited.status_code == 200  # the same generic answer, not a tell-tale 429
    assert "We could not find" in limited.content.decode()
    assert "confirm withdrawal" not in limited.content.decode().lower()


# --- Pre-contractual information (art. 49(1)(h))


def test_the_information_page_says_where_the_function_is(client: Client) -> None:
    response = client.get(reverse("consumer_information"))
    assert response.status_code == 200
    html = response.content.decode()
    assert reverse("withdrawal") in html
    assert "14 days" in html
    assert "withdraw from contract here" in html.lower()


def test_the_information_page_follows_the_period_setting(client: Client, settings: Any) -> None:
    settings.WITHDRAWAL_PERIOD_DAYS = 30
    assert "30 days" in client.get(reverse("consumer_information")).content.decode()


def test_the_information_page_in_italian_uses_the_italian_label(client: Client) -> None:
    html = client.get("/it/consumer-information/").content.decode().lower()
    assert "recedere dal contratto qui" in html


def test_the_information_page_marks_the_guarantee_notice_as_a_draft(client: Client) -> None:
    html = client.get(reverse("consumer_information")).content.decode().lower()
    assert "legal guarantee" in html
    assert "24 months" in html
