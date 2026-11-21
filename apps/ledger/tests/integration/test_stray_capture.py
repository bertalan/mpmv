"""PAY.4 (owner decision 2026-11-21): a stray capture alerts support and is not refunded.

A payment that succeeds on an order already cancelled, or with an amount or currency that does
not match the order, is not refunded automatically. Staff get one alert per payment, the audit
entry carries no personal data, the order state does not change, no refund is sent to the
provider, and the money stays received and unallocated (no ledger line, no transfer).
"""

import json
import logging
import secrets
import time
from collections.abc import Iterator
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.core.models import AuditLog
from apps.ledger.models import LedgerEntry
from apps.orders.models import Order
from apps.orders.services import transition_order
from apps.orders.tests.integration.test_place_order import CTX, place, sellable
from apps.payments import registry, stray, stripe_engine
from apps.payments.changeset_keys import PAYMENT_STRAY_FLAGGED
from apps.payments.models import Payment
from apps.payments.stripe_provider import StripePaymentProvider, provider_from_settings
from apps.payments.tests.fake_stripe import FakeStripeClient

pytestmark = pytest.mark.django_db

SECRET = secrets.token_urlsafe(24)


@pytest.fixture
def stripe(settings: Any) -> Iterator[FakeStripeClient]:
    client = FakeStripeClient(confirm=False)
    settings.PAYMENT_PROVIDER = "stripe"
    registry.unregister_provider("stripe")
    registry.register_provider(lambda: StripePaymentProvider(client, webhook_secret=SECRET))
    yield client
    registry.unregister_provider("stripe")
    registry.register_provider(provider_from_settings)


def capture(
    client: Client,
    order: Order,
    *,
    minor: int | None = None,
    currency: str = "eur",
    event_id: str = "evt_stray",
) -> int:
    amount = stripe_engine.to_minor(order.grand_total, "EUR") if minor is None else minor
    body = json.dumps(
        {
            "id": event_id,
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": order.payment_intent_ref,
                    "amount": amount,
                    "amount_received": amount,
                    "currency": currency,
                    "latest_charge": "ch_stray",
                    "metadata": {"order_ref": order.number},
                }
            },
        }
    ).encode()
    header = stripe_engine.sign_payload(body, SECRET, int(time.time()))
    response = client.post(
        reverse("payments_webhook"),
        data=body,
        content_type="application/json",
        headers={"Stripe-Signature": header},
    )
    return int(response.status_code)


def alerts(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if stray.ALERT_KEY in record.getMessage() and record.levelno == logging.ERROR
    ]


def assert_stray(
    order: Order, stripe: FakeStripeClient, caplog: pytest.LogCaptureFixture, *, state: str
) -> None:
    order.refresh_from_db()
    assert order.state == state
    assert len(alerts(caplog)) == 1
    entry = AuditLog.objects.get(action=PAYMENT_STRAY_FLAGGED)
    assert entry.target_key == order.payment_intent_ref
    assert entry.after is not None
    assert set(entry.after) == {"order_ref", "reason", "amount", "currency"}
    assert not LedgerEntry.objects.filter(vendor_order__order=order).exists()
    assert "create_refund" not in stripe.calls
    assert stripe._refunded == {}
    assert stripe.transfer_calls == []
    assert Payment.objects.filter(provider_ref=order.payment_intent_ref).count() == 1


def test_a_capture_on_a_cancelled_order_alerts_once_and_refunds_nothing(
    stripe: FakeStripeClient, client: Client, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR, logger="mpmv.alerts")
    order = place(sellable())
    transition_order(order, "cancelled", ctx=CTX)
    assert capture(client, order) == 200
    assert_stray(order, stripe, caplog, state="cancelled")
    assert (AuditLog.objects.get(action=PAYMENT_STRAY_FLAGGED).after or {}).get("reason") == "late"


@pytest.mark.parametrize(("delta", "currency"), [(-1, "eur"), (0, "usd"), (1, "eur")])
def test_a_capture_of_another_amount_or_currency_alerts_once_and_refunds_nothing(
    stripe: FakeStripeClient,
    client: Client,
    caplog: pytest.LogCaptureFixture,
    delta: int,
    currency: str,
) -> None:
    caplog.set_level(logging.ERROR, logger="mpmv.alerts")
    order = place(sellable())
    minor = stripe_engine.to_minor(order.grand_total, "EUR") + delta
    assert capture(client, order, minor=minor, currency=currency) == 200
    assert_stray(order, stripe, caplog, state="awaiting_payment")
    assert (AuditLog.objects.get(action=PAYMENT_STRAY_FLAGGED).after or {}).get(
        "reason"
    ) == "mismatch"


def test_a_replayed_webhook_raises_no_second_alert(
    stripe: FakeStripeClient, client: Client, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR, logger="mpmv.alerts")
    order = place(sellable())
    transition_order(order, "cancelled", ctx=CTX)
    assert capture(client, order) == 200
    assert capture(client, order) == 200  # the same event again
    assert capture(client, order, event_id="evt_stray_again") == 200  # another event, same payment
    payment = Payment.objects.get(provider_ref=order.payment_intent_ref)
    assert stray.flag_stray_capture(payment, reason="late", ctx=CTX).is_empty  # direct retry
    assert_stray(order, stripe, caplog, state="cancelled")


def test_a_dry_run_reports_the_flag_and_writes_nothing(
    stripe: FakeStripeClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR, logger="mpmv.alerts")
    order = place(sellable())
    payment = Payment(
        provider="stripe",
        provider_ref=order.payment_intent_ref,
        order_ref=order.number,
        amount=order.grand_total,
        currency="USD",
        succeeded_at=order.created_at,
    )
    changes = stray.flag_stray_capture(payment, reason="mismatch", ctx=CTX, dry_run=True)
    assert not changes.is_empty
    assert not AuditLog.objects.filter(action=PAYMENT_STRAY_FLAGGED).exists()
    assert alerts(caplog) == []


def test_a_normal_capture_raises_no_alert(
    stripe: FakeStripeClient, client: Client, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR, logger="mpmv.alerts")
    order = place(sellable())
    assert capture(client, order) == 200
    order.refresh_from_db()
    assert order.state == "paid"
    assert alerts(caplog) == []
    assert not AuditLog.objects.filter(action=PAYMENT_STRAY_FLAGGED).exists()


def test_the_alert_and_the_audit_carry_no_personal_data(
    stripe: FakeStripeClient, client: Client, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR, logger="mpmv.alerts")
    order = place(sellable())
    transition_order(order, "cancelled", ctx=CTX)
    capture(client, order)
    entry = AuditLog.objects.get(action=PAYMENT_STRAY_FLAGGED)
    personal = [value for value in (order.email, order.customer_name) if value]
    assert personal
    text = json.dumps(entry.after) + " ".join(alerts(caplog))
    for value in personal:
        assert value not in text
