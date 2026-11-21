"""PAY.4 follow-up (owner decisions 2026-11-21): orphan captures, the manual close of a
stray-payment alert, and the nightly 14-day auto-close.

A capture that matches no order, or carries a wrong order reference, raises the stray-capture
alert with no refund. Support closes an alert by hand, audited without the reason text. The
nightly job closes an alert 14 days old only when the provider confirms a full refund; otherwise
it reminds support once a day and keeps the alert open.
"""

import dataclasses
import datetime as dt
import json
import logging
import threading
import time
from decimal import Decimal
from typing import Any

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.tests.factories import UserFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.ledger.tests.integration import test_stray_capture as base
from apps.orders.models import Order
from apps.orders.tests.integration.test_place_order import CTX, place, sellable
from apps.payments import engine, stray, stripe_engine
from apps.payments.changeset_keys import (
    PAYMENT_STRAY_CLOSED,
    PAYMENT_STRAY_FLAGGED,
    PAYMENT_STRAY_REMINDED,
)
from apps.payments.models import Payment
from apps.payments.tests.fake_stripe import FakeStripeClient

pytestmark = pytest.mark.django_db

stripe = base.stripe  # the PAY.4 fake Stripe fixture
alerts = base.alerts
SECRET = base.SECRET

REASON = "Refunded by hand in the provider dashboard."


def webhook(
    client: Client, *, intent: str, order_ref: str, minor: int, event_id: str = "evt_orphan"
) -> int:
    body = json.dumps(
        {
            "id": event_id,
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": intent,
                    "amount": minor,
                    "amount_received": minor,
                    "currency": "eur",
                    "latest_charge": f"ch_{intent}",
                    "metadata": {"order_ref": order_ref},
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


def reason_of(ref: str) -> str:
    entry = AuditLog.objects.get(action=PAYMENT_STRAY_FLAGGED, target_key=ref)
    return str((entry.after or {}).get("reason"))


def test_an_orphan_capture_alerts_once_and_a_replay_does_not(
    stripe: FakeStripeClient, client: Client, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR, logger="mpmv.alerts")
    assert webhook(client, intent="pi_orphan", order_ref="NO-SUCH-ORDER", minor=1234) == 200
    assert webhook(client, intent="pi_orphan", order_ref="NO-SUCH-ORDER", minor=1234) == 200
    assert (
        webhook(client, intent="pi_orphan", order_ref="NO-SUCH-ORDER", minor=1234, event_id="e2")
        == 200
    )
    assert reason_of("pi_orphan") == "unmatched"
    assert len(alerts(caplog)) == 1
    assert "create_refund" not in stripe.calls


def test_a_capture_with_a_wrong_reference_alerts(
    stripe: FakeStripeClient, client: Client, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR, logger="mpmv.alerts")
    order = place(sellable())
    minor = stripe_engine.to_minor(order.grand_total, "EUR")
    assert webhook(client, intent="pi_other", order_ref=order.number, minor=minor) == 200
    order.refresh_from_db()
    assert order.state == "awaiting_payment"
    assert reason_of("pi_other") == "wrong_reference"
    assert len(alerts(caplog)) == 1
    assert "create_refund" not in stripe.calls


def later(days: int) -> dt.datetime:
    """The audit log is append-only, so the job runs "in the future" instead of backdating."""
    return timezone.now() + dt.timedelta(days=days, minutes=1)


def flagged(order: Order) -> Payment:
    payment = Payment.objects.create(
        provider="stripe",
        provider_ref=order.payment_intent_ref,
        charge_ref="ch_stray",
        order_ref=order.number,
        amount=order.grand_total,
        currency="EUR",
        succeeded_at=timezone.now(),
    )
    stray.flag_stray_capture(payment, reason="late", ctx=CTX)
    return payment


def staff_ctx(user: Any) -> AuditContext:
    return AuditContext(actor_label="staff:test", source=AuditLog.Source.ADMIN, actor_id=user.pk)


def test_a_manual_close_is_audited_without_the_reason_and_happens_once(
    stripe: FakeStripeClient,
) -> None:
    payment = flagged(place(sellable()))
    support = UserFactory.create(platform_role="support")
    dry = stray.close_stray_capture(
        payment.provider_ref, actor=support, reason=REASON, ctx=staff_ctx(support), dry_run=True
    )
    assert not dry.is_empty
    assert not AuditLog.objects.filter(action=PAYMENT_STRAY_CLOSED).exists()
    changes = stray.close_stray_capture(
        payment.provider_ref, actor=support, reason=REASON, ctx=staff_ctx(support)
    )
    assert not changes.is_empty
    entry = AuditLog.objects.get(action=PAYMENT_STRAY_CLOSED)
    assert entry.actor_id == support.pk
    assert (entry.after or {})["by"] == "support"
    assert REASON not in json.dumps(entry.after)
    assert stray.open_stray_captures() == []
    again = stray.close_stray_capture(
        payment.provider_ref, actor=support, reason=REASON, ctx=staff_ctx(support)
    )
    assert again.is_empty
    assert AuditLog.objects.filter(action=PAYMENT_STRAY_CLOSED).count() == 1


@pytest.mark.django_db(transaction=True)
def test_two_concurrent_closes_write_one_entry() -> None:
    payment = flagged(place(sellable()))
    # A flush by an earlier transactional test drops the role groups (a data migration), so the
    # closer here is the superuser; the permission check has its own tests above and below.
    support = UserFactory.create(superuser=True)
    barrier = threading.Barrier(2)
    results: list[bool] = []

    def close_once() -> None:
        try:
            barrier.wait(timeout=10)
            changes = stray.close_stray_capture(
                payment.provider_ref, actor=support, reason=REASON, ctx=staff_ctx(support)
            )
            results.append(changes.is_empty)
        finally:
            connection.close()

    threads = [threading.Thread(target=close_once) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(results) == [False, True]
    assert AuditLog.objects.filter(action=PAYMENT_STRAY_CLOSED).count() == 1


@pytest.mark.parametrize("role", ["finance", "catalog", None])
def test_only_support_or_the_platform_owner_may_close(
    stripe: FakeStripeClient, role: str | None
) -> None:
    payment = flagged(place(sellable()))
    user = UserFactory.create(platform_role=role) if role else UserFactory.create()
    with pytest.raises(PermissionDenied):
        stray.close_stray_capture(
            payment.provider_ref, actor=user, reason=REASON, ctx=staff_ctx(user)
        )
    assert not AuditLog.objects.filter(action=PAYMENT_STRAY_CLOSED).exists()


def test_a_close_without_a_reason_is_refused(stripe: FakeStripeClient) -> None:
    payment = flagged(place(sellable()))
    support = UserFactory.create(platform_role="support")
    with pytest.raises(ValidationError):
        stray.close_stray_capture(
            payment.provider_ref, actor=support, reason="  ", ctx=staff_ctx(support)
        )
    assert not AuditLog.objects.filter(action=PAYMENT_STRAY_CLOSED).exists()


def with_charge(stripe: FakeStripeClient, payment: Payment, refunded: Decimal) -> None:
    """The fake provider knows the payment's charge, with `refunded` already given back."""
    stripe.intents[payment.provider_ref] = dataclasses.replace(
        stripe.intents[payment.provider_ref], charge_id=payment.charge_ref
    )
    if refunded:
        stripe.refund_in_dashboard(payment.provider_ref, stripe_engine.to_minor(refunded, "EUR"))


def test_the_nightly_job_closes_a_14_day_old_alert_with_a_full_refund(
    stripe: FakeStripeClient,
) -> None:
    payment = flagged(place(sellable()))
    night = later(14)
    with_charge(stripe, payment, payment.amount)
    dry = stray.sweep_stray_captures(now=night, dry_run=True)
    assert not dry.is_empty
    assert not AuditLog.objects.filter(action=PAYMENT_STRAY_CLOSED).exists()
    stray.sweep_stray_captures(now=night)
    entry = AuditLog.objects.get(action=PAYMENT_STRAY_CLOSED)
    assert (entry.after or {})["by"] == "auto"
    assert Decimal(str((entry.after or {})["refunded"])) == payment.amount  # the provider's figure
    assert stray.open_stray_captures() == []
    assert stray.sweep_stray_captures(now=night).is_empty


@pytest.mark.parametrize("refunded", [Decimal("0"), Decimal("1.00")])
def test_without_a_full_refund_the_job_reminds_once_a_day_and_keeps_it_open(
    stripe: FakeStripeClient, caplog: pytest.LogCaptureFixture, refunded: Decimal
) -> None:
    caplog.set_level(logging.ERROR, logger="mpmv.alerts")
    payment = flagged(place(sellable()))
    night = later(20)
    with_charge(stripe, payment, refunded)
    assert not stray.sweep_stray_captures(now=night, dry_run=True).is_empty
    assert not AuditLog.objects.filter(action=PAYMENT_STRAY_REMINDED).exists()
    stray.sweep_stray_captures(now=night)
    assert stray.sweep_stray_captures(now=night).is_empty  # the rerun the same night
    assert AuditLog.objects.filter(action=PAYMENT_STRAY_REMINDED).count() == 1
    assert not AuditLog.objects.filter(action=PAYMENT_STRAY_CLOSED).exists()
    assert [p.provider_ref for p in stray.open_stray_captures()] == [payment.provider_ref]
    reminders = [m for m in caplog.messages if stray.REMINDER_KEY in m]
    assert len(reminders) == 1
    tomorrow = night + dt.timedelta(days=1)
    assert not stray.sweep_stray_captures(now=tomorrow).is_empty  # a new day, a new reminder


def test_the_job_does_nothing_before_14_days(stripe: FakeStripeClient) -> None:
    payment = flagged(place(sellable()))
    with_charge(stripe, payment, payment.amount)
    assert stray.sweep_stray_captures(now=later(13)).is_empty
    assert [p.provider_ref for p in stray.open_stray_captures()] == [payment.provider_ref]


def test_the_engine_step() -> None:
    now = timezone.now()
    old = now - dt.timedelta(days=14)
    amount = Decimal("10.00")
    step = engine.stray_alert_step
    assert step(flagged_at=now, now=now, amount=amount, refunded=amount, reminded_today=False) == (
        engine.STRAY_WAIT
    )
    assert step(flagged_at=old, now=now, amount=amount, refunded=amount, reminded_today=True) == (
        engine.STRAY_CLOSE
    )
    assert step(flagged_at=old, now=now, amount=amount, refunded=None, reminded_today=False) == (
        engine.STRAY_REMIND
    )
    assert step(
        flagged_at=old, now=now, amount=amount, refunded=Decimal("9.99"), reminded_today=True
    ) == (engine.STRAY_WAIT)
