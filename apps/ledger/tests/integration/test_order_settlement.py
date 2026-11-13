"""PAY.3 (D-08): ledger lines and one transfer per vendor package from a paid order.

Stripe is doubled only behind our own `StripeClient` (`FakeStripeClient`). Worked numbers (rule:
10% of goods, collection cost 1.40% of the gross + 0.25, VAT 22%, shipping 5.00 per vendor):

- vendor A, 2 x 10.00: goods 20.00, VAT 4.40, shipping 5.00 + 1.10 -> gross 30.50;
  commission 2.00, fee 30.50 x 1.4% + 0.25 = 0.677 -> 0.68, net 27.82.
- vendor B, 3 x 3.33: goods 9.99, VAT 2.20 (2.1978), shipping 5.00 + 1.10 -> gross 18.29;
  commission 0.999 -> 1.00, fee 18.29 x 1.4% + 0.25 = 0.50606 -> 0.51, net 16.78.
- order 48.79 = shares 27.82 + 16.78 + platform fee 4.19 (commissions 3.00 + fees 1.19).
"""

import dataclasses
import datetime as dt
import json
import secrets
import time
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.core import tasks
from apps.core.models import AuditLog
from apps.core.services import FLAG_CACHE
from apps.core.tests.factories import PlatformOperatorFactory
from apps.ledger import engine as ledger_engine
from apps.ledger import services as ledger_services
from apps.ledger import settlement
from apps.ledger.models import LedgerEntry
from apps.ledger.services import vendor_balance
from apps.orders.models import Order, VendorOrder
from apps.orders.services import place_order
from apps.orders.tests.integration.test_place_order import (
    COMMISSION,
    CTX,
    NOW,
    line,
    preview,
    sellable,
    vendor_of,
)
from apps.payments import registry, stripe_engine
from apps.payments.engine import PaymentRequest, PaymentResult, TransferRequest
from apps.payments.models import ConnectedAccount, Payment
from apps.payments.stripe_client import StripeError
from apps.payments.stripe_provider import StripePaymentProvider
from apps.payments.tests.fake_stripe import FakeStripeClient

pytestmark = pytest.mark.django_db

SECRET = secrets.token_urlsafe(24)
D = Decimal
# The shape `CommissionRule.snapshot()` freezes: the shared test snapshot uses placeholder bases.
RULE = {**COMMISSION, "base": "net_goods", "fee_base": "gross_paid", "promo_label": ""}


class SpyProvider(StripePaymentProvider):
    """The real Stripe provider over the fake client, remembering the payment requests."""

    requests: list[PaymentRequest]

    def create_payment(self, request: PaymentRequest) -> PaymentResult:
        self.requests.append(request)
        return super().create_payment(request)


@pytest.fixture(autouse=True)
def _setup() -> Iterator[None]:
    FLAG_CACHE.clear()
    PlatformOperatorFactory.create(
        valid_from=dt.datetime(2020, 1, 1, tzinfo=dt.UTC), open_ended=True
    )
    yield
    FLAG_CACHE.clear()


@pytest.fixture
def stripe(settings: Any) -> Iterator[FakeStripeClient]:
    client = FakeStripeClient(confirm=False)
    provider = SpyProvider(client, webhook_secret=SECRET)
    provider.requests = []
    client.spy = provider  # type: ignore[attr-defined]
    settings.PAYMENT_PROVIDER = "stripe"
    registry.unregister_provider("stripe")
    registry.register_provider(lambda: provider)
    yield client
    registry.unregister_provider("stripe")
    from apps.payments.stripe_provider import provider_from_settings

    registry.register_provider(provider_from_settings)


def two_vendor_order() -> Order:
    product_a = sellable()
    product_b = sellable(price_net=D("3.33"))
    order, _ = place_order(
        preview(
            vendor_of(product_a, line(product_a, 2), commission_snapshot=RULE),
            vendor_of(product_b, line(product_b, 3), commission_snapshot=RULE),
        ),
        ctx=CTX,
        now=NOW,
    )
    assert order is not None
    return order


def pay(stripe: FakeStripeClient, order: Order, event_id: str = "evt_1") -> Any:
    """The customer pays: the intent gets its charge, then the signed webhook arrives."""
    intent = stripe.intents[order.payment_intent_ref]
    charge = f"ch_{secrets.token_hex(5)}"
    stripe.intents[intent.id] = dataclasses.replace(intent, status="succeeded", charge_id=charge)
    minor = stripe_engine.to_minor(order.grand_total, "EUR")
    body = json.dumps(
        {
            "id": event_id,
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": intent.id,
                    "amount": minor,
                    "amount_received": minor,
                    "currency": "eur",
                    "latest_charge": charge,
                    "metadata": {"order_ref": order.number},
                }
            },
        }
    ).encode()
    header = stripe_engine.sign_payload(body, SECRET, int(time.time()))
    return Client().post(
        reverse("payments_webhook"),
        data=body,
        content_type="application/json",
        headers={"Stripe-Signature": header},
    )


def entries(vendor_order: Any) -> dict[str, Decimal]:
    return {e.kind: e.amount for e in LedgerEntry.objects.filter(vendor_order=vendor_order)}


# --- the worked two-vendor order --------------------------------------------------------------


def test_the_payment_intent_carries_the_real_shares_and_the_platform_fee(
    stripe: FakeStripeClient,
) -> None:
    order = two_vendor_order()
    (request,) = stripe.spy.requests  # type: ignore[attr-defined]
    assert request.amount == D("48.79") == order.grand_total
    assert sorted(s.amount for s in request.shares) == [D("16.78"), D("27.82")]
    assert request.platform_fee == D("4.19")
    assert sum((s.amount for s in request.shares), D("0")) + request.platform_fee == request.amount
    accounts = {a.account_ref for a in ConnectedAccount.objects.all()}
    assert {s.vendor_account_ref for s in request.shares} == accounts


def test_a_paid_order_posts_the_lines_and_queues_one_transfer_job(
    stripe: FakeStripeClient,
) -> None:
    order = two_vendor_order()
    assert pay(stripe, order).status_code == 200
    order.refresh_from_db()
    assert order.state == "paid"
    vendor_a, vendor_b = order.vendor_orders.order_by("id")
    assert entries(vendor_a) == {
        "sale": D("24.40"),
        "shipping": D("6.10"),
        "commission": D("-2.00"),
        "payment_fee": D("-0.68"),
    }
    assert entries(vendor_b) == {
        "sale": D("12.19"),
        "shipping": D("6.10"),
        "commission": D("-1.00"),
        "payment_fee": D("-0.51"),
    }
    assert vendor_balance(vendor_a.vendor) == D("27.82")
    assert vendor_balance(vendor_b.vendor) == D("16.78")
    assert stripe.transfers == {}  # the transfers leave after the commit, from the job
    (job,) = tasks.list_jobs("ledger.transfer_payment", status="todo")
    assert job.task_name == "ledger.transfer_payment"
    assert AuditLog.objects.filter(action="ledger.entry_recorded").count() == 8


def test_the_transfer_job_sends_one_transfer_per_vendor_tied_to_the_charge(
    stripe: FakeStripeClient,
) -> None:
    order = two_vendor_order()
    pay(stripe, order)
    payment = Payment.objects.get()
    outcome = settlement.transfer_payment(payment)
    assert len(outcome.transfers) == 2
    vendor_a, vendor_b = order.vendor_orders.order_by("id")
    sent = sorted(stripe.transfers.values(), key=lambda t: t.amount_minor)
    assert [t.amount_minor for t in sent] == [1678, 2782]
    assert {t.transfer_group for t in sent} == {order.number}
    assert {c["source_transaction"] for c in stripe.transfer_calls} == {payment.charge_ref}
    assert {c["destination"] for c in stripe.transfer_calls} == {
        f"acct_test_{vendor_a.vendor_id}",
        f"acct_test_{vendor_b.vendor_id}",
    }
    assert entries(vendor_a)["transfer"] == D("-27.82")
    assert entries(vendor_b)["transfer"] == D("-16.78")
    assert vendor_balance(vendor_a.vendor) == vendor_balance(vendor_b.vendor) == D("0.00")
    transfer = LedgerEntry.objects.get(vendor_order=vendor_a, kind="transfer")
    assert transfer.external_ref in stripe.transfers
    assert transfer.gateway_account == f"acct_test_{vendor_a.vendor_id}"


def test_a_redelivery_is_a_no_op(stripe: FakeStripeClient) -> None:
    order = two_vendor_order()
    pay(stripe, order)
    settlement.transfer_payment(Payment.objects.get())
    before = LedgerEntry.objects.count(), len(stripe.transfers), len(tasks.list_jobs())
    assert pay(stripe, order, event_id="evt_1").status_code == 200  # same event id
    assert pay(stripe, order, event_id="evt_2").status_code == 200  # a second event, same payment
    again = settlement.transfer_payment(Payment.objects.get())
    assert (LedgerEntry.objects.count(), len(stripe.transfers), len(tasks.list_jobs())) == before
    assert again.posted == 0
    assert again.transfers == []
    assert Payment.objects.count() == 1


# --- failure, retry --------------------------------------------------------------------------


def test_a_failed_transfer_is_audited_then_retried_into_one_transfer(
    stripe: FakeStripeClient,
) -> None:
    order = two_vendor_order()
    pay(stripe, order)
    payment = Payment.objects.get()
    real = stripe.create_transfer
    calls = {"n": 0}

    def flaky(**kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] <= 2:
            raise StripeError("Stripe is down")
        return real(**kwargs)

    stripe.create_transfer = flaky  # type: ignore[method-assign]
    first = settlement.transfer_payment(payment)
    assert first.failed == 2
    assert first.transfers == []
    order.refresh_from_db()
    assert order.state == "paid"  # the order stays paid
    assert LedgerEntry.objects.filter(kind="transfer").count() == 0
    assert LedgerEntry.objects.filter(kind="sale").count() == 2  # lines never waited
    assert AuditLog.objects.filter(action="ledger.transfer_failed").count() == 2
    assert settlement.pending_payments() == [payment]
    second = settlement.settle_pending()
    assert len(second.transfers) == 2
    assert len(stripe.transfers) == 2
    assert LedgerEntry.objects.filter(kind="transfer").count() == 2
    assert settlement.pending_payments() == []
    assert settlement.settle_pending().transfers == []
    assert len(stripe.transfers) == 2


def test_a_transfer_that_succeeded_but_was_not_recorded_is_found_again_not_repeated(
    stripe: FakeStripeClient,
) -> None:
    order = two_vendor_order()
    pay(stripe, order)
    payment = Payment.objects.get()
    settlement.transfer_payment(payment)
    sent = dict(stripe.transfers)
    # The ledger transfer lines are lost (they cannot be: the model is append-only; so the retry
    # is simulated by a new idempotency path): the provider must not create a second transfer.
    provider = registry.configured_provider()
    vendor_a = order.vendor_orders.order_by("id").first()
    assert vendor_a is not None
    again = provider.transfer(  # type: ignore[union-attr]
        TransferRequest(
            order.number,
            payment.provider_ref,
            payment.charge_ref,
            f"acct_test_{vendor_a.vendor_id}",
            D("27.82"),
            "EUR",
            "another-key-after-24-hours",
        )
    )
    assert again.transfer_ref in sent
    assert stripe.transfers == sent


# --- vendors that cannot be paid -------------------------------------------------------------


def test_a_vendor_without_a_ready_account_gets_lines_and_no_transfer_until_it_is_ready(
    stripe: FakeStripeClient,
) -> None:
    order = two_vendor_order()
    _, vendor_b = order.vendor_orders.order_by("id")
    ConnectedAccount.objects.filter(vendor=vendor_b.vendor).update(status="restricted")
    pay(stripe, order)
    outcome = settlement.transfer_payment(Payment.objects.get())
    assert outcome.skipped == 1
    assert len(stripe.transfers) == 1  # only vendor A
    assert vendor_balance(vendor_b.vendor) == D("16.78")  # the receivable, never lost
    assert not LedgerEntry.objects.filter(vendor_order=vendor_b, kind="transfer").exists()
    settlement.settle_pending()  # still not ready: audited once, not every sweep
    assert AuditLog.objects.filter(action="ledger.transfer_without_account").count() == 1
    ConnectedAccount.objects.filter(vendor=vendor_b.vendor).update(status="active")
    settlement.settle_pending()
    assert len(stripe.transfers) == 2
    assert vendor_balance(vendor_b.vendor) == D("0.00")


def test_a_vendor_order_with_an_unusable_snapshot_is_blocked_and_audited_not_posted_at_zero(
    stripe: FakeStripeClient,
) -> None:
    product_a, product_b = sellable(), sellable()
    broken = {**RULE, "percent": "150.00"}  # a rule the engine refuses
    order, _ = place_order(
        preview(
            vendor_of(product_a, line(product_a), commission_snapshot=RULE),
            vendor_of(product_b, line(product_b), commission_snapshot=broken),
        ),
        ctx=CTX,
        now=NOW,
    )
    assert order is not None
    _, vendor_b = order.vendor_orders.order_by("id")
    (request,) = stripe.spy.requests  # type: ignore[attr-defined]
    assert len(request.shares) == 1  # the blocked vendor has no share
    assert sum((s.amount for s in request.shares), D("0")) + request.platform_fee == request.amount
    pay(stripe, order)
    outcome = settlement.transfer_payment(Payment.objects.get())
    assert outcome.blocked == 1
    assert not LedgerEntry.objects.filter(vendor_order=vendor_b).exists()
    assert AuditLog.objects.filter(action="ledger.posting_blocked").count() == 1
    settlement.settle_pending()  # a standing problem is audited once, not on every sweep
    assert AuditLog.objects.filter(action="ledger.posting_blocked").count() == 1
    assert len(stripe.transfers) == 1  # vendor A is paid regardless
    assert Order.objects.get(pk=order.pk).state == "paid"


# --- rounding --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("price", "qty"),
    [("3.33", 3), ("0.01", 1), ("9.99", 7), ("0.07", 5)],
)
def test_every_package_nets_to_zero_after_its_transfer_and_shares_plus_fee_equal_the_charge(
    stripe: FakeStripeClient, price: str, qty: int
) -> None:
    product = sellable(price_net=D(price))
    place = vendor_of(product, line(product, qty), commission_snapshot=RULE)
    order, _ = place_order(preview(place), ctx=CTX, now=NOW)
    assert order is not None
    (request,) = stripe.spy.requests  # type: ignore[attr-defined]
    assert sum((s.amount for s in request.shares), D("0")) + request.platform_fee == request.amount
    assert request.platform_fee >= 0
    pay(stripe, order)
    settlement.transfer_payment(Payment.objects.get())
    (only,) = order.vendor_orders.all()
    assert vendor_balance(only.vendor) == D("0.00")
    assert all(e.amount == e.amount.quantize(D("0.01")) for e in LedgerEntry.objects.all())
    (transfer,) = stripe.transfers.values()
    assert D(transfer.amount_minor) / 100 == request.shares[0].amount


# --- outbox: the transfer job is a row written inside the paying transaction ------------------


def test_a_rolled_back_paying_transaction_leaves_no_paid_order_no_line_no_job_no_transfer(
    stripe: FakeStripeClient,
) -> None:
    """The webhook transaction marks the order paid, posts the ledger lines and queues the job
    (Procrastinate defers through the Django connection). A receiver that fails after all that
    rolls the whole transaction back: the order is still awaiting payment, and nothing is sent."""
    from apps.payments.models import ProcessedEvent
    from apps.payments.signals import on_order_paid

    class Boom(Exception):
        pass

    def failing(sender: Any, **kwargs: Any) -> None:
        # By now the order is paid, the lines are written and the job is queued, in this savepoint.
        assert Order.objects.get(pk=order.pk).state == "paid"
        assert LedgerEntry.objects.exists()
        assert tasks.list_jobs("ledger.transfer_payment", status="todo")
        raise Boom

    order = two_vendor_order()
    on_order_paid.connect(failing, weak=False)
    try:
        with pytest.raises(Boom):
            pay(stripe, order)
    finally:
        on_order_paid.disconnect(failing)

    assert Order.objects.get(pk=order.pk).state == "awaiting_payment"
    assert Payment.objects.count() == 0
    assert ProcessedEvent.objects.count() == 0
    assert LedgerEntry.objects.count() == 0
    assert tasks.list_jobs("ledger.transfer_payment") == []
    assert stripe.transfers == {}
    # The provider's retry runs everything again and settles normally.
    assert pay(stripe, order).status_code == 200
    assert len(tasks.list_jobs("ledger.transfer_payment", status="todo")) == 1


# --- Stripe succeeded, our write failed ---------------------------------------------------------


def test_a_transfer_created_at_stripe_whose_line_failed_is_found_by_the_retry_not_paid_twice(
    stripe: FakeStripeClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    order = two_vendor_order()
    pay(stripe, order)
    payment = Payment.objects.get()
    real = ledger_services.record_entry

    def failing_transfer_write(*args: Any, **kwargs: Any) -> Any:
        if kwargs.get("kind") == "transfer":
            raise ledger_engine.LedgerError("disk full")
        return real(*args, **kwargs)

    monkeypatch.setattr(ledger_services, "record_entry", failing_transfer_write)
    first = settlement.transfer_payment(payment)
    assert first.failed == 2
    assert first.transfers == []
    assert len(stripe.transfers) == 2  # the money moved at Stripe
    assert LedgerEntry.objects.filter(kind="transfer").count() == 0
    sent = dict(stripe.transfers)
    monkeypatch.undo()
    stripe._replies.clear()  # the 24 h idempotency keys expired
    again = settlement.settle_pending()
    assert stripe.transfers == sent  # found by destination and payment, no second transfer
    assert sorted(again.transfers) == sorted(sent)
    recorded = {e.external_ref for e in LedgerEntry.objects.filter(kind="transfer")}
    assert recorded == set(sent)
    for vendor_order in order.vendor_orders.all():
        assert vendor_balance(vendor_order.vendor) == D("0.00")


def test_three_vendors_in_one_order_get_three_correct_transfers(stripe: FakeStripeClient) -> None:
    products = [sellable(), sellable(price_net=D("3.33")), sellable(price_net=D("7.00"))]
    order, _ = place_order(
        preview(*[vendor_of(p, line(p, 2), commission_snapshot=RULE) for p in products]),
        ctx=CTX,
        now=NOW,
    )
    assert order is not None
    (request,) = stripe.spy.requests  # type: ignore[attr-defined]
    assert len(request.shares) == 3
    assert sum((s.amount for s in request.shares), D("0")) + request.platform_fee == request.amount
    pay(stripe, order)
    outcome = settlement.transfer_payment(Payment.objects.get())
    assert len(outcome.transfers) == 3
    assert len(stripe.transfers) == 3
    assert sorted(t.amount_minor for t in stripe.transfers.values()) == sorted(
        int(s.amount * 100) for s in request.shares
    )
    for vendor_order in order.vendor_orders.all():
        assert vendor_balance(vendor_order.vendor) == D("0.00")
    assert settlement.settle_pending().transfers == []
    assert len(stripe.transfers) == 3


# --- refund or cancellation before a pending transfer -------------------------------------------


def skipped_audits() -> Any:
    return AuditLog.objects.filter(action="ledger.transfer_skipped_refunded")


def paid_with_transfers_pending(stripe: FakeStripeClient) -> tuple[Order, Payment]:
    """Paid, ledger lines posted, every transfer failing so far (Stripe down)."""
    order = two_vendor_order()
    pay(stripe, order)
    payment = Payment.objects.get()
    real = stripe.create_transfer

    def down(**kwargs: Any) -> Any:
        raise StripeError("Stripe is down")

    stripe.create_transfer = down  # type: ignore[method-assign]
    assert settlement.transfer_payment(payment).failed == 2
    stripe.create_transfer = real  # type: ignore[method-assign]
    return order, payment


def test_a_dashboard_refund_before_the_sweep_pays_nobody_and_is_audited_once(
    stripe: FakeStripeClient,
) -> None:
    order, payment = paid_with_transfers_pending(stripe)
    stripe.refund_in_dashboard(payment.provider_ref, 4879)  # the full charge, order still "paid"
    assert Order.objects.get(pk=order.pk).state == "paid"
    first = settlement.settle_pending()
    second = settlement.settle_pending()
    assert first.transfers == second.transfers == []
    assert first.refunded == 2
    assert stripe.transfers == {}
    assert not LedgerEntry.objects.filter(kind="transfer").exists()
    assert skipped_audits().count() == 2  # one per vendor package, not one per sweep
    assert {a.after["reason"] for a in skipped_audits()} == {"charge refunded at the provider"}


def test_a_partial_dashboard_refund_pays_nobody_and_is_audited_for_staff(
    stripe: FakeStripeClient,
) -> None:
    _, payment = paid_with_transfers_pending(stripe)
    stripe.refund_in_dashboard(payment.provider_ref, 100)  # 1.00 of 48.79
    settlement.settle_pending()
    settlement.settle_pending()
    assert stripe.transfers == {}
    assert skipped_audits().count() == 2


@pytest.mark.parametrize("state", ["refunded", "partially_refunded"])
def test_an_order_already_refunded_in_our_states_is_not_paid_by_the_sweep(
    stripe: FakeStripeClient, state: str
) -> None:
    order, _ = paid_with_transfers_pending(stripe)
    Order.objects.filter(pk=order.pk).update(state="partially_refunded")
    if state == "refunded":
        Order.objects.filter(pk=order.pk).update(state="refunded")
    outcome = settlement.settle_pending()
    assert outcome.transfers == []
    assert stripe.transfers == {}
    assert skipped_audits().count() == 2
    assert {a.after["reason"] for a in skipped_audits()} == {f"order is {state}"}


def test_a_cancelled_package_is_skipped_and_the_others_are_paid(
    stripe: FakeStripeClient,
) -> None:
    order, _ = paid_with_transfers_pending(stripe)
    vendor_a, vendor_b = order.vendor_orders.order_by("id")
    VendorOrder.objects.filter(pk=vendor_a.pk).update(state="cancelled")
    outcome = settlement.settle_pending()
    assert len(outcome.transfers) == 1  # vendor B only
    assert not LedgerEntry.objects.filter(vendor_order=vendor_a, kind="transfer").exists()
    assert LedgerEntry.objects.filter(vendor_order=vendor_b, kind="transfer").exists()
    assert skipped_audits().get().target_key == str(vendor_a.public_id)


def test_a_charge_that_cannot_be_read_is_not_paid_and_is_retried(
    stripe: FakeStripeClient,
) -> None:
    paid_with_transfers_pending(stripe)
    real = stripe.get_charge

    def down(charge_id: str) -> Any:
        raise StripeError("Stripe is down")

    stripe.get_charge = down  # type: ignore[method-assign]
    assert settlement.settle_pending().transfers == []
    assert stripe.transfers == {}
    stripe.get_charge = real  # type: ignore[method-assign]
    assert len(settlement.settle_pending().transfers) == 2  # nothing refunded: paid on retry


def test_an_unrefunded_order_is_paid_and_the_charge_is_read_once_per_call(
    stripe: FakeStripeClient,
) -> None:
    pay(stripe, two_vendor_order())
    outcome = settlement.transfer_payment(Payment.objects.get())
    assert len(outcome.transfers) == 2
    assert outcome.refunded == 0
    assert stripe.calls.count("get_charge") == 1
    assert not skipped_audits().exists()


# --- commission VAT mode (spec 04 section 4.4 case 3, VEN.34) ----------------------------------


def test_a_platform_with_vat_on_commissions_settles_nothing_until_ven_34(
    stripe: FakeStripeClient,
) -> None:
    from apps.core.models import PlatformOperator

    PlatformOperator.objects.update(commission_vat_mode="standard")
    order = two_vendor_order()
    pay(stripe, order)
    payment = Payment.objects.get()
    first = settlement.transfer_payment(payment)
    again = settlement.settle_pending()
    assert first.blocked == 1
    assert again.transfers == []
    assert LedgerEntry.objects.count() == 0
    assert stripe.transfers == {}
    assert Order.objects.get(pk=order.pk).state == "paid"
    audits = AuditLog.objects.filter(action="ledger.settlement_blocked_vat_mode")
    assert audits.count() == 1  # once per order, not on every sweep
    assert audits.get().target_key == order.number
    PlatformOperator.objects.update(commission_vat_mode="none")  # fixed: the sweep settles
    assert len(settlement.settle_pending().transfers) == 2


def test_mode_none_settles_as_before(stripe: FakeStripeClient) -> None:
    order = two_vendor_order()
    pay(stripe, order)
    assert len(settlement.transfer_payment(Payment.objects.get()).transfers) == 2
    assert not AuditLog.objects.filter(action="ledger.settlement_blocked_vat_mode").exists()
