"""PAY.2: the signed, idempotent webhook endpoint and the `on_payment_succeeded` hook."""

import json
import secrets
import time
from collections.abc import Iterator
from typing import Any

import pytest
from django.test import Client, override_settings
from django.urls import reverse

from apps.core.models import AuditLog
from apps.payments import registry, stripe_engine
from apps.payments.models import Payment, ProcessedEvent
from apps.payments.signals import on_payment_succeeded
from apps.payments.stripe_provider import StripePaymentProvider
from apps.payments.tests.factories import PaymentFactory, ProcessedEventFactory
from apps.payments.tests.fake_stripe import FakeStripeClient

pytestmark = pytest.mark.django_db

SECRET = secrets.token_urlsafe(24)


@pytest.fixture(autouse=True)
def stripe(settings: Any) -> Iterator[None]:
    settings.PAYMENT_PROVIDER = "stripe"
    registry.unregister_provider("stripe")
    registry.register_provider(
        lambda: StripePaymentProvider(FakeStripeClient(), webhook_secret=SECRET)
    )
    yield
    registry.unregister_provider("stripe")
    from apps.payments.stripe_provider import provider_from_settings

    registry.register_provider(provider_from_settings)


def event(event_id: str = "evt_1", intent_id: str = "pi_1", amount: int = 12_345) -> bytes:
    return json.dumps(
        {
            "id": event_id,
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": intent_id,
                    "amount": amount,
                    "amount_received": amount,
                    "currency": "eur",
                    "latest_charge": "ch_1",
                    "metadata": {"order_ref": "order-7"},
                }
            },
        }
    ).encode()


def post(client: Client, body: bytes, secret: str = SECRET, at: float | None = None) -> Any:
    header = stripe_engine.sign_payload(body, secret, int(at or time.time()))
    return client.post(
        reverse("payments_webhook"),
        data=body,
        content_type="application/json",
        headers={"Stripe-Signature": header},
    )


def test_a_signed_event_records_the_payment_and_audits_it(client: Client) -> None:
    assert post(client, event()).status_code == 200
    payment = Payment.objects.get()
    assert (payment.provider_ref, payment.order_ref, payment.charge_ref) == (
        "pi_1",
        "order-7",
        "ch_1",
    )
    assert str(payment.amount) == "123.45"
    assert ProcessedEvent.objects.filter(provider="stripe", event_id="evt_1").exists()
    entry = AuditLog.objects.get(action="payments.payment.succeeded")
    assert entry.actor_label == "webhook:payments"


def test_a_replayed_event_is_a_no_op(client: Client) -> None:
    received: list[Payment] = []

    def receiver(payment: Payment, **_: Any) -> None:
        received.append(payment)

    on_payment_succeeded.connect(receiver, weak=False)
    body = event()
    try:
        assert post(client, body).status_code == 200
        assert post(client, body).status_code == 200
    finally:
        on_payment_succeeded.disconnect(receiver)
    assert Payment.objects.count() == 1
    assert ProcessedEvent.objects.count() == 1
    assert AuditLog.objects.filter(action="payments.payment.succeeded").count() == 1
    assert len(received) == 1


def test_a_second_event_for_the_same_payment_does_not_record_it_twice(client: Client) -> None:
    assert post(client, event("evt_1")).status_code == 200
    assert post(client, event("evt_2")).status_code == 200
    assert Payment.objects.count() == 1
    assert ProcessedEvent.objects.count() == 2


def test_a_bad_signature_is_refused_and_stores_nothing(client: Client) -> None:
    assert post(client, event(), secret=secrets.token_urlsafe(24)).status_code == 400
    assert post(client, event(), at=time.time() - 3600).status_code == 400
    response = client.post(
        reverse("payments_webhook"), data=event(), content_type="application/json"
    )
    assert response.status_code == 400
    assert not Payment.objects.exists()
    assert not ProcessedEvent.objects.exists()


def test_a_signed_event_without_payment_fields_stores_nothing(client: Client) -> None:
    body = json.dumps(
        {"id": "evt_x", "type": "payment_intent.succeeded", "data": {"object": {}}}
    ).encode()
    assert post(client, body).status_code == 400
    assert not ProcessedEvent.objects.exists()  # Stripe's retry may succeed later


def test_other_event_types_are_recorded_as_seen_and_ignored(client: Client) -> None:
    body = json.dumps({"id": "evt_o", "type": "charge.updated", "data": {}}).encode()
    assert post(client, body).status_code == 200
    assert ProcessedEvent.objects.get().event_type == "charge.updated"
    assert not Payment.objects.exists()


def test_a_failing_receiver_rolls_the_event_back_so_the_retry_runs_again(client: Client) -> None:
    def boom(**_: Any) -> None:
        raise RuntimeError("ledger down")

    on_payment_succeeded.connect(boom, weak=False)
    try:
        with pytest.raises(RuntimeError):
            post(Client(raise_request_exception=True), event())
    finally:
        on_payment_succeeded.disconnect(boom)
    assert not Payment.objects.exists()
    assert not ProcessedEvent.objects.exists()
    assert post(client, event()).status_code == 200


def test_the_endpoint_is_off_without_a_provider_and_post_only(client: Client) -> None:
    with override_settings(PAYMENT_PROVIDER=""):
        assert post(client, event()).status_code == 404
    assert client.get(reverse("payments_webhook")).status_code == 405


def test_the_endpoint_needs_no_csrf_token() -> None:
    strict = Client(enforce_csrf_checks=True)
    assert post(strict, event()).status_code == 200


def test_an_event_id_seen_before_is_a_no_op_even_with_no_payment_row(client: Client) -> None:
    ProcessedEventFactory.create(event_id="evt_1")
    assert post(client, event("evt_1")).status_code == 200
    assert not Payment.objects.exists()


def test_a_payment_already_recorded_is_not_recorded_again(client: Client) -> None:
    PaymentFactory.create(provider_ref="pi_1")
    assert post(client, event("evt_new", "pi_1")).status_code == 200
    assert Payment.objects.count() == 1
    assert not AuditLog.objects.filter(action="payments.payment.succeeded").exists()


def test_a_dry_run_reports_and_stores_nothing() -> None:
    from apps.payments import services

    body = event()
    header = stripe_engine.sign_payload(body, SECRET, int(time.time()))
    result = services.process_webhook(body, header, dry_run=True)
    assert result.outcome is services.Outcome.PROCESSED
    assert not ProcessedEvent.objects.exists()
    assert not Payment.objects.exists()


def test_the_same_url_accepts_the_connect_secret_only_when_it_is_set(client: Client) -> None:
    connect = secrets.token_urlsafe(24)
    body = event()
    assert post(client, body, secret=connect).status_code == 400
    registry.unregister_provider("stripe")
    registry.register_provider(
        lambda: StripePaymentProvider(
            FakeStripeClient(), webhook_secret=SECRET, connect_webhook_secret=connect
        )
    )
    assert post(client, body, secret=connect).status_code == 200
    assert post(client, body, secret=SECRET).status_code == 200  # replay: idempotent no-op
    assert post(client, body, secret=secrets.token_urlsafe(24)).status_code == 400
