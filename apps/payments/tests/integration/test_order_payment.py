"""ORD.4, PAY.1, PAY.2: checkout starts the one payment of an order; the webhook marks it paid.

D-08, separate charges and transfers: ONE PaymentIntent per order on the platform account, with
`transfer_group` = the order number (the per-vendor transfers are PAY.3, not tested here). Stripe
is doubled only behind our own `StripeClient` interface (`FakeStripeClient`); no real key.
"""

import json
import secrets
import time
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.core import flags
from apps.core.models import AuditLog
from apps.core.services import FLAG_CACHE
from apps.orders import services
from apps.orders.models import Order, StockReservation
from apps.orders.services import CheckoutClosed, place_order
from apps.orders.tests.integration.test_kill_switch import pull
from apps.orders.tests.integration.test_place_order import (
    CTX,
    NOW,
    line,
    place,
    preview,
    sellable,
    vendor_of,
)
from apps.payments import registry, stripe_engine
from apps.payments.models import Payment, ProcessedEvent
from apps.payments.stripe_client import StripeError
from apps.payments.stripe_provider import StripePaymentProvider
from apps.payments.tests.fake_stripe import FakeStripeClient

pytestmark = pytest.mark.django_db

SECRET = secrets.token_urlsafe(24)


@pytest.fixture(autouse=True)
def _fresh_flag_cache() -> Iterator[None]:
    FLAG_CACHE.clear()
    yield
    FLAG_CACHE.clear()


@pytest.fixture
def stripe(settings: Any) -> Iterator[FakeStripeClient]:
    client = FakeStripeClient(confirm=False)  # the customer has not paid when the intent exists
    settings.PAYMENT_PROVIDER = "stripe"
    registry.unregister_provider("stripe")
    registry.register_provider(lambda: StripePaymentProvider(client, webhook_secret=SECRET))
    yield client
    registry.unregister_provider("stripe")
    from apps.payments.stripe_provider import provider_from_settings

    registry.register_provider(provider_from_settings)


def failing(**kwargs: Any) -> Any:
    raise StripeError("down")


def intent_calls(client: FakeStripeClient) -> int:
    return client.calls.count("create_payment_intent")


def webhook(
    client: Client, order: Order, kind: str = "payment_intent.succeeded", event_id: str = "evt_1"
) -> Any:
    minor = stripe_engine.to_minor(order.grand_total, "EUR")
    body = json.dumps(
        {
            "id": event_id,
            "type": kind,
            "data": {
                "object": {
                    "id": order.payment_intent_ref,
                    "amount": minor,
                    "amount_received": minor,
                    "currency": "eur",
                    "latest_charge": "ch_1",
                    "metadata": {"order_ref": order.number},
                }
            },
        }
    ).encode()
    header = stripe_engine.sign_payload(body, SECRET, int(time.time()))
    return client.post(
        reverse("payments_webhook"),
        data=body,
        content_type="application/json",
        headers={"Stripe-Signature": header},
    )


# --- place_order starts the payment -----------------------------------------------------------


def test_placing_creates_one_payment_with_the_gross_total_and_the_order_as_transfer_group(
    stripe: FakeStripeClient,
) -> None:
    order = place(sellable(), 2)
    assert intent_calls(stripe) == 1
    intent = stripe.intents[order.payment_intent_ref]
    assert intent.amount_minor == int(order.grand_total * 100)
    assert intent.currency == "EUR"
    assert intent.transfer_group == order.number == order.transfer_group
    assert order.payment_client_secret.startswith(order.payment_intent_ref)
    assert order.state == "awaiting_payment"
    assert AuditLog.objects.filter(action="orders.payment_started").count() == 1


def test_a_second_start_for_the_same_order_creates_no_second_payment(
    stripe: FakeStripeClient,
) -> None:
    order = place(sellable())
    assert services.start_payment(order, ctx=CTX) == ""
    assert services.start_payment(order, ctx=CTX) == ""
    assert intent_calls(stripe) == 1


def test_each_order_has_its_own_payment(stripe: FakeStripeClient) -> None:
    first, second = place(sellable()), place(sellable())
    assert first.payment_intent_ref != second.payment_intent_ref
    assert intent_calls(stripe) == 2


def test_the_client_secret_is_not_stored_on_the_order(stripe: FakeStripeClient) -> None:
    order = place(sellable())
    stored = Order.objects.get(pk=order.pk)
    assert order.payment_client_secret not in {
        str(value) for value in stored.__dict__.values() if isinstance(value, str)
    }


def test_no_provider_configured_places_the_order_without_a_payment(settings: Any) -> None:
    settings.PAYMENT_PROVIDER = ""
    order = place(sellable())
    assert order.payment_intent_ref == ""
    assert order.payment_client_secret == ""
    assert order.state == "awaiting_payment"


def test_a_provider_failure_keeps_the_order_and_a_retry_creates_the_payment(
    stripe: FakeStripeClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = stripe.create_payment_intent

    monkeypatch.setattr(stripe, "create_payment_intent", failing)
    order = place(sellable())
    assert order.payment_intent_ref == ""
    assert order.payment_client_secret == ""
    monkeypatch.setattr(stripe, "create_payment_intent", real)
    assert services.start_payment(order, ctx=CTX)
    assert intent_calls(stripe) == 1


def test_the_payments_switch_refuses_a_new_payment(stripe: FakeStripeClient) -> None:
    product = sellable()
    pull(flags.PAYMENTS)
    with pytest.raises(CheckoutClosed):
        place_order(preview(vendor_of(product, line(product))), ctx=CTX, now=NOW)
    assert intent_calls(stripe) == 0
    assert Order.objects.count() == 0


def test_the_switch_pulled_after_placing_stops_the_retry_but_not_the_order(
    stripe: FakeStripeClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(stripe, "create_payment_intent", failing)
    order = place(sellable())
    pull(flags.PAYMENTS)
    with pytest.raises(CheckoutClosed):
        services.start_payment(order, ctx=CTX)
    assert Order.objects.get(pk=order.pk).state == "awaiting_payment"


# --- the webhook marks the order paid ---------------------------------------------------------


def test_the_webhook_marks_the_order_paid_once_decrements_stock_and_audits(
    stripe: FakeStripeClient, client: Client
) -> None:
    product = sellable(stock_qty=5)
    order = place(product, 2)
    assert webhook(client, order).status_code == 200
    order.refresh_from_db()
    product.refresh_from_db()
    assert order.state == "paid"
    assert order.paid_at is not None
    assert product.stock_qty == 3
    assert not StockReservation.objects.filter(order=order).exists()
    assert Payment.objects.get().order_ref == order.number
    assert AuditLog.objects.filter(action="orders.transition", actor_label="webhook:payments")
    assert AuditLog.objects.filter(action="orders.stock_decremented").count() == 1


def test_a_redelivered_event_is_a_no_op(stripe: FakeStripeClient, client: Client) -> None:
    product = sellable(stock_qty=5)
    order = place(product, 2)
    assert webhook(client, order).status_code == 200
    transitions = AuditLog.objects.filter(action="orders.transition").count()
    assert webhook(client, order).status_code == 200  # same event id
    assert webhook(client, order, event_id="evt_2").status_code == 200  # same payment
    product.refresh_from_db()
    assert product.stock_qty == 3
    assert AuditLog.objects.filter(action="orders.transition").count() == transitions
    assert Payment.objects.count() == 1
    assert ProcessedEvent.objects.count() == 2


def test_a_failed_payment_event_leaves_the_order_unpaid_and_audited(
    stripe: FakeStripeClient, client: Client
) -> None:
    product = sellable(stock_qty=5)
    order = place(product)
    assert webhook(client, order, "payment_intent.payment_failed").status_code == 200
    order.refresh_from_db()
    product.refresh_from_db()
    assert order.state == "awaiting_payment"
    assert order.paid_at is None
    assert product.stock_qty == 5
    assert StockReservation.objects.filter(order=order).exists()
    entry = AuditLog.objects.get(action="orders.payment_failed")
    assert entry.target_key == order.number
    assert not Payment.objects.exists()


def test_a_payment_of_another_amount_does_not_pay_the_order(
    stripe: FakeStripeClient, client: Client
) -> None:
    order = place(sellable())
    body = json.dumps(
        {
            "id": "evt_short",
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": order.payment_intent_ref,
                    "amount": int(order.grand_total * 100) - 1,
                    "currency": "eur",
                    "metadata": {"order_ref": order.number},
                }
            },
        }
    ).encode()
    header = stripe_engine.sign_payload(body, SECRET, int(time.time()))
    client.post(
        reverse("payments_webhook"),
        data=body,
        content_type="application/json",
        headers={"Stripe-Signature": header},
    )
    order.refresh_from_db()
    assert order.state == "awaiting_payment"
    assert AuditLog.objects.filter(action="orders.payment_mismatch").count() == 1


def test_a_payment_for_an_unknown_order_is_audited_and_moves_nothing(
    stripe: FakeStripeClient,
) -> None:
    from apps.orders.services import mark_order_paid

    assert not mark_order_paid("NOPE-1", amount=Decimal("1.00"), currency="EUR", ctx=CTX)
    assert AuditLog.objects.filter(action="orders.payment_unmatched").count() == 1


def test_a_late_payment_on_a_cancelled_order_is_audited(stripe: FakeStripeClient) -> None:
    from apps.orders.services import mark_order_paid, transition_order

    order = place(sellable())
    transition_order(order, "cancelled", ctx=CTX)
    assert not mark_order_paid(order.number, amount=order.grand_total, currency="EUR", ctx=CTX)
    assert AuditLog.objects.filter(action="orders.payment_late").count() == 1


def test_the_payment_page_gets_the_client_secret_and_a_csp_for_stripe_js(
    stripe: FakeStripeClient, settings: Any, client: Client
) -> None:
    settings.STRIPE_PUBLISHABLE_KEY = "pk_" + secrets.token_urlsafe(12)
    order = place(sellable())
    session = client.session
    session["checkout_confirmation"] = str(order.public_id)
    session["checkout_payment_secret"] = order.payment_client_secret
    session.save()
    response = client.get(reverse("orders:confirmation"))
    html = response.content.decode()
    assert response.status_code == 200
    assert f'data-client-secret="{order.payment_client_secret}"' in html
    assert "payment.js" in html
    assert "<script>" not in html
    policy = response.headers["Content-Security-Policy"]
    assert "https://js.stripe.com" in policy
    assert "unsafe-inline" not in policy
