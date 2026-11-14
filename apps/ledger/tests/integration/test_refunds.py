"""PAY.4 (D-05, D-41, L-10): refunds through and outside the platform, the duplicate payment.

Stripe is doubled only behind our own `StripeClient` (`FakeStripeClient`). The #409 numbers: a
vendor order of 122.00 (goods 100.00 + VAT 22.00; rule 20% commission, collection cost 4% + 2.00
= 6.88), refund of 61.00 with a provider cost of 1.20: commission back 10.00, collection cost back
3.44, refund fee ceil(1.20 + 2.00) = 4.00, vendor clawback 61 - 10 - 3.44 + 4 = 51.56. The
provider takes back the vendor's share, 61 - 10 - 3.44 = 47.56, from its transfer; the refund fee
stays a debit on the vendor's balance.
"""

import dataclasses
import datetime as dt
import json
import logging
import secrets
import time
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.tests.factories import UserFactory
from apps.core import tasks
from apps.core.audit import AuditContext
from apps.core.models import AuditLog, PlatformOperator
from apps.core.services import FLAG_CACHE
from apps.core.tests.factories import PlatformOperatorFactory
from apps.ledger import refunds, settlement
from apps.ledger.models import LedgerEntry, Refund
from apps.ledger.refund_engine import RefundSelection
from apps.ledger.services import vendor_balance
from apps.ledger.tests.factories import RefundFactory
from apps.orders.models import Order, VendorOrder
from apps.orders.services import place_order
from apps.orders.tests.integration.test_place_order import (
    CTX,
    NOW,
    line,
    preview,
    sellable,
    vendor_of,
)
from apps.payments import duplicates, registry, stripe_engine
from apps.payments.models import Payment
from apps.payments.stripe_client import StripeError
from apps.payments.stripe_provider import StripePaymentProvider
from apps.payments.tests.fake_stripe import FakeStripeClient
from apps.vendors.refund_fee import ReasonRequired
from apps.vendors.tests.factories import VendorMemberFactory

pytestmark = pytest.mark.django_db

D = Decimal
SECRET = secrets.token_urlsafe(24)
# The #409 rule: 20% of the goods, collection cost 4% of the gross + 2.00.
RULE = {
    "rule_id": "r409",
    "version": 1,
    "percent": "20.00",
    "base": "net_goods",
    "fee_percent": "4.00",
    "fee_fixed": "2.00",
    "fee_base": "gross_paid",
    "promo_label": "",
}
STAFF_CTX = AuditContext(actor_label="staff:test", source=AuditLog.Source.ADMIN)
VENDOR_CTX = AuditContext(actor_label="vendor:test", source=AuditLog.Source.VENDOR)
AMOUNT = RefundSelection


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
    client = FakeStripeClient(confirm=False, refund_fee_minor=120)
    provider = StripePaymentProvider(client, webhook_secret=SECRET)
    settings.PAYMENT_PROVIDER = "stripe"
    registry.unregister_provider("stripe")
    registry.register_provider(lambda: provider)
    yield client
    registry.unregister_provider("stripe")
    from apps.payments.stripe_provider import provider_from_settings

    registry.register_provider(provider_from_settings)


def place_409(rule: dict[str, Any] | None = None) -> Order:
    """One vendor, goods 100.00 net, VAT 22.00, no shipping: gross 122.00 (as #409)."""
    product = sellable(price_net=D("100.00"))
    order, _ = place_order(
        preview(
            vendor_of(
                product,
                line(product, 1),
                shipping_net=D("0.00"),
                shipping_tax=None,
                commission_snapshot=rule or RULE,
            )
        ),
        ctx=CTX,
        now=NOW,
    )
    assert order is not None
    assert order.grand_total == D("122.00")
    return order


def webhook(stripe: FakeStripeClient, order: Order, intent_id: str, event_id: str) -> Any:
    intent = stripe.intents[intent_id]
    charge = intent.charge_id or f"ch_{secrets.token_hex(5)}"
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


def pay(stripe: FakeStripeClient, order: Order, *, transfer: bool = True) -> VendorOrder:
    assert webhook(stripe, order, order.payment_intent_ref, "evt_pay").status_code == 200
    if transfer:
        settlement.transfer_payment(Payment.objects.get(provider_ref=order.payment_intent_ref))
    return order.vendor_orders.get()


def member_of(sub: VendorOrder) -> Any:
    return VendorMemberFactory.create(vendor=sub.vendor, role="staff").user


def rid() -> str:
    return secrets.token_urlsafe(12)


def kinds(sub: VendorOrder) -> dict[str, Decimal]:
    return {e.kind: e.amount for e in LedgerEntry.objects.filter(vendor_order=sub)}


def refund_through(sub: VendorOrder, selection: RefundSelection, **kwargs: Any) -> Any:
    return refunds.refund_through_platform(
        sub,
        selection,
        request_id=kwargs.pop("request_id", rid()),
        actor=kwargs.pop("actor", None) or member_of(sub),
        ctx=VENDOR_CTX,
        **kwargs,
    )


def set_operator(**fields: Any) -> PlatformOperator:
    """A new operator version from now on (ARC.7: versioned, read at refund time)."""
    current = PlatformOperator.objects.get(valid_to__isnull=True)
    start = timezone.now() - dt.timedelta(seconds=5)
    PlatformOperator.objects.filter(pk=current.pk).update(valid_to=start)
    values = {
        f.name: getattr(current, f.name)
        for f in PlatformOperator._meta.concrete_fields
        if f.name not in {"id", "public_id", "valid_from", "valid_to", "created_at", "updated_at"}
    }
    values.update(fields)
    return PlatformOperatorFactory.create(valid_from=start, open_ended=True, **values)


# --- the #409 numbers, through the platform ---------------------------------------------------


def test_the_409_numbers_in_the_ledger_and_at_the_provider(stripe: FakeStripeClient) -> None:
    order = place_409()
    sub = pay(stripe, order)
    transfer = LedgerEntry.objects.get(vendor_order=sub, kind="transfer")
    assert transfer.amount == D("-95.12")

    changes = refund_through(sub, AMOUNT("amount", amount=D("61.00")))

    refund = changes.refund
    assert refund is not None
    assert refund.provider_cost == D("1.20")
    assert refund.commission_reversal == D("10.00")
    assert refund.payment_fee_reversal == D("3.44")
    assert refund.refund_fee == D("4.00")
    assert kinds(sub) == {
        "sale": D("122.00"),
        "commission": D("-20.00"),
        "payment_fee": D("-6.88"),
        "transfer": D("-95.12"),
        "refund": D("-61.00"),
        "commission_reversal": D("10.00"),
        "payment_fee_reversal": D("3.44"),
        "refund_fee": D("-4.00"),
        "transfer_reversal": D("47.56"),
    }
    # clawback 51.56 = refund - reversals + fee; 47.56 comes back from the transfer, 4.00 stays
    # a debit on the vendor's balance (the platform service line, netted against later transfers)
    assert D("61.00") - D("10.00") - D("3.44") + D("4.00") == D("51.56")
    assert vendor_balance(sub.vendor) == D("-4.00")
    assert [(t, m) for t, m in stripe.reversals] == [(transfer.external_ref, 4756)]
    assert stripe.calls.count("create_refund") == 1
    order.refresh_from_db()
    sub.refresh_from_db()
    assert order.state == "partially_refunded"
    assert sub.state == "pending"
    assert AuditLog.objects.filter(action="ledger.refund_recorded").count() == 1


def test_by_line_by_quantity_and_by_amount_each_issue_one_reverse_transfer(
    stripe: FakeStripeClient,
) -> None:
    product_a, product_b = sellable(price_net=D("10.00")), sellable(price_net=D("20.00"))
    other = sellable(price_net=D("5.00"))
    order, _ = place_order(
        preview(
            vendor_of(product_a, line(product_a, 3), line(product_b, 1), commission_snapshot=RULE),
            vendor_of(other, line(other, 1), commission_snapshot=RULE),
        ),
        ctx=CTX,
        now=NOW,
    )
    assert order is not None
    webhook(stripe, order, order.payment_intent_ref, "evt_pay")
    settlement.transfer_payment(Payment.objects.get())
    sub = order.vendor_orders.order_by("id").first()
    assert sub is not None
    first_line, second_line = sub.lines.order_by("id")
    assert len(stripe.transfers) == 2

    refund_through(sub, AMOUNT("quantity", ((first_line.pk, 1),)))
    assert len(stripe.reversals) == 1
    refund_through(sub, AMOUNT("line", ((second_line.pk, 1),)))
    assert len(stripe.reversals) == 2
    refund_through(sub, AMOUNT("amount", amount=D("5.00")))
    assert len(stripe.reversals) == 3
    # only this vendor's own transfer is ever reversed, never the other vendor's
    mine = LedgerEntry.objects.get(vendor_order=sub, kind="transfer").external_ref
    assert {t for t, _ in stripe.reversals} == {mine}
    gross = [r.refunded_gross for r in Refund.objects.order_by("id")]
    assert gross == [D("12.20"), D("24.40"), D("5.00")]


def test_a_full_refund_gives_back_all_the_fees_and_closes_the_package_and_order(
    stripe: FakeStripeClient,
) -> None:
    order = place_409()
    sub = pay(stripe, order)
    refund_through(sub, AMOUNT("amount", amount=D("122.00")))
    assert kinds(sub)["commission_reversal"] == D("20.00")
    assert kinds(sub)["payment_fee_reversal"] == D("6.88")
    assert kinds(sub)["refund_fee"] == D("-4.00")  # one refund fee line
    assert kinds(sub)["transfer_reversal"] == D("95.12")
    sub.refresh_from_db()
    order.refresh_from_db()
    assert sub.state == "cancelled"  # never accepted: the machine has no pending -> refunded
    assert order.state == "refunded"
    # a further refund is refused: nothing is left
    with pytest.raises(refunds.RefundRefused):
        refund_through(sub, AMOUNT("amount", amount=D("1.00")))


def test_a_second_partial_refund_writes_its_own_fee_and_caps_the_reversals(
    stripe: FakeStripeClient,
) -> None:
    sub = pay(stripe, place_409())
    refund_through(sub, AMOUNT("amount", amount=D("61.00")))
    refund_through(sub, AMOUNT("amount", amount=D("61.00")))
    fees = LedgerEntry.objects.filter(vendor_order=sub, kind="refund_fee")
    assert [e.amount for e in fees] == [D("-4.00"), D("-4.00")]  # every event carries it
    # the two halves together give back at most what was paid: 20.00 and 6.88
    assert kinds_sum(sub, "commission_reversal") == D("20.00")
    assert kinds_sum(sub, "payment_fee_reversal") == D("6.88")


def kinds_sum(sub: VendorOrder, kind: str) -> Decimal:
    return sum((e.amount for e in LedgerEntry.objects.filter(vendor_order=sub, kind=kind)), D("0"))


def test_the_fee_is_never_a_line_for_the_consumer(stripe: FakeStripeClient) -> None:
    sub = pay(stripe, place_409())
    refund_through(sub, AMOUNT("amount", amount=D("61.00")))
    Refund.objects.get()
    intent = stripe.intents[sub.order.payment_intent_ref]
    assert stripe._refunded[intent.id] == 6100  # the buyer gets the whole 61.00 back, no fee off


# --- refusals, idempotency, dry run -----------------------------------------------------------


def test_a_repeat_with_the_same_request_id_refunds_once(stripe: FakeStripeClient) -> None:
    sub = pay(stripe, place_409())
    request_id = rid()
    first = refund_through(sub, AMOUNT("amount", amount=D("10.00")), request_id=request_id)
    second = refund_through(sub, AMOUNT("amount", amount=D("10.00")), request_id=request_id)
    assert first.refund is not None
    assert second.is_empty
    assert Refund.objects.count() == 1
    assert stripe.calls.count("create_refund") == 1
    assert LedgerEntry.objects.filter(vendor_order=sub, kind="refund").count() == 1


def test_a_request_id_cannot_be_reused_for_another_request(stripe: FakeStripeClient) -> None:
    sub = pay(stripe, place_409())
    request_id = rid()
    refund_through(sub, AMOUNT("amount", amount=D("10.00")), request_id=request_id)
    with pytest.raises(refunds.RefundRefused):
        refund_through(sub, AMOUNT("amount", amount=D("20.00")), request_id=request_id)
    assert Refund.objects.count() == 1


def test_more_than_is_left_a_bad_request_id_and_a_stranger_are_refused(
    stripe: FakeStripeClient,
) -> None:
    sub = pay(stripe, place_409())
    with pytest.raises(refunds.RefundRefused):
        refund_through(sub, AMOUNT("amount", amount=D("122.01")))
    with pytest.raises(refunds.RefundRefused):
        refund_through(sub, AMOUNT("amount", amount=D("1.00")), request_id="short")
    with pytest.raises(PermissionDenied):
        refunds.refund_through_platform(
            sub, AMOUNT("amount", amount=D("1.00")), request_id=rid(),
            actor=UserFactory.create(), ctx=VENDOR_CTX,
        )  # fmt: skip
    assert not Refund.objects.exists()
    assert stripe.calls.count("create_refund") == 0


def test_an_unpaid_order_is_refused(stripe: FakeStripeClient) -> None:
    order = place_409()
    sub = order.vendor_orders.get()
    with pytest.raises(refunds.RefundRefused):
        refund_through(sub, AMOUNT("amount", amount=D("1.00")))


def test_a_provider_failure_writes_nothing_and_can_be_repeated(
    stripe: FakeStripeClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sub = pay(stripe, place_409())
    before = LedgerEntry.objects.count()
    real = stripe.create_refund
    request_id = rid()

    def down(**kwargs: Any) -> Any:
        raise StripeError("Stripe is down")

    monkeypatch.setattr(stripe, "create_refund", down)
    with pytest.raises(refunds.RefundFailed):
        refund_through(sub, AMOUNT("amount", amount=D("61.00")), request_id=request_id)
    assert LedgerEntry.objects.count() == before
    assert not Refund.objects.exists()
    monkeypatch.setattr(stripe, "create_refund", real)
    refund_through(sub, AMOUNT("amount", amount=D("61.00")), request_id=request_id)
    assert Refund.objects.count() == 1


def test_a_dry_run_changes_nothing_and_returns_the_change_set(stripe: FakeStripeClient) -> None:
    sub = pay(stripe, place_409())
    before = LedgerEntry.objects.count(), AuditLog.objects.count()
    changes = refund_through(sub, AMOUNT("amount", amount=D("61.00")), dry_run=True)
    assert [c.model for c in changes.changes] == ["ledger.refund"]
    assert changes.refund is None
    assert (LedgerEntry.objects.count(), AuditLog.objects.count()) == before
    assert not Refund.objects.exists()
    assert stripe.calls.count("create_refund") == 0
    sub.order.refresh_from_db()
    assert sub.order.state == "paid"


# --- platform-initiated refunds, L-10 ---------------------------------------------------------


def test_a_platform_error_refund_charges_no_fee_gives_the_fees_back_and_is_audited(
    stripe: FakeStripeClient,
) -> None:
    sub = pay(stripe, place_409())
    staff = UserFactory.create(staff=True)
    refunds.refund_by_platform(
        sub, AMOUNT("amount", amount=D("61.00")), cause="platform_error", request_id=rid(),
        actor=staff, ctx=STAFF_CTX, platform_error_reason="Double capture bug in our checkout",
    )  # fmt: skip
    assert "refund_fee" not in kinds(sub)
    assert kinds(sub)["commission_reversal"] == D("10.00")
    assert kinds(sub)["payment_fee_reversal"] == D("3.44")
    refund = Refund.objects.get()
    assert refund.platform_error
    assert refund.channel == "platform_initiated"
    assert refund.refund_fee == D("0.00")
    flagged = AuditLog.objects.get(action="ledger.refund_fee_excluded")
    assert flagged.after is not None
    assert flagged.after["reason"] == "Double capture bug in our checkout"
    assert flagged.after["set_by"] == "staff:test"
    assert vendor_balance(sub.vendor) == D("0.00")  # the ledger sums to zero: no fee left


def test_the_platform_error_flag_needs_a_reason_and_staff(stripe: FakeStripeClient) -> None:
    sub = pay(stripe, place_409())
    staff = UserFactory.create(staff=True)
    with pytest.raises(ReasonRequired):
        refunds.refund_by_platform(
            sub, AMOUNT("amount", amount=D("5.00")), cause="platform_error", request_id=rid(),
            actor=staff, ctx=STAFF_CTX, platform_error_reason="   ",
        )  # fmt: skip
    with pytest.raises(refunds.StaffRequired):
        refunds.refund_by_platform(
            sub, AMOUNT("amount", amount=D("5.00")), cause="platform_error", request_id=rid(),
            actor=member_of(sub), ctx=VENDOR_CTX, platform_error_reason="a reason",
        )  # fmt: skip
    with pytest.raises(refunds.RefundRefused):  # a vendor default never excludes the fee
        refunds.refund_by_platform(
            sub, AMOUNT("amount", amount=D("5.00")), cause="vendor_default", request_id=rid(),
            actor=staff, ctx=STAFF_CTX, platform_error_reason="a reason",
        )  # fmt: skip
    assert not Refund.objects.exists()


def test_a_vendor_default_refund_by_the_platform_charges_the_fee(
    stripe: FakeStripeClient,
) -> None:
    sub = pay(stripe, place_409())
    refunds.refund_by_platform(
        sub, AMOUNT("amount", amount=D("61.00")), cause="vendor_default", request_id=rid(),
        actor=UserFactory.create(staff=True), ctx=STAFF_CTX,
    )  # fmt: skip
    assert kinds(sub)["refund_fee"] == D("-4.00")
    assert Refund.objects.get().cause == "vendor_default"


def test_a_case_the_operator_switched_off_is_refused(stripe: FakeStripeClient) -> None:
    sub = pay(stripe, place_409())
    set_operator(platform_refund_cases=["vendor_default"])
    with pytest.raises(refunds.RefundRefused):
        refunds.refund_by_platform(
            sub, AMOUNT("amount", amount=D("5.00")), cause="platform_error", request_id=rid(),
            actor=UserFactory.create(staff=True), ctx=STAFF_CTX,
            platform_error_reason="our bug",
        )  # fmt: skip


# --- operator settings (ARC.7), read at refund time -------------------------------------------


def test_with_commission_reversal_off_no_reversal_line_is_written(
    stripe: FakeStripeClient,
) -> None:
    sub = pay(stripe, place_409())
    set_operator(reverse_fees_on_vendor_refund=False)
    refund_through(sub, AMOUNT("amount", amount=D("61.00")))
    assert "commission_reversal" not in kinds(sub)
    assert "payment_fee_reversal" not in kinds(sub)
    assert kinds(sub)["refund_fee"] == D("-4.00")
    assert kinds(sub)["transfer_reversal"] == D("61.00")  # the whole refunded gross comes back


def test_a_later_operator_surcharge_does_not_change_the_fee_of_an_existing_order(
    stripe: FakeStripeClient,
) -> None:
    sub = pay(stripe, place_409())
    assert sub.commission_snapshot["refund_fee_surcharge"] == "2.00"  # frozen at placement
    set_operator(refund_fee_surcharge=D("3.00"))  # a version after the order was placed
    refund_through(sub, AMOUNT("amount", amount=D("61.00")))
    assert kinds(sub)["refund_fee"] == D("-4.00")  # ceil(1.20 + 2.00)


def test_an_order_without_a_frozen_surcharge_uses_the_version_at_its_creation() -> None:
    later = set_operator(refund_fee_surcharge=D("9.00"))
    second = dt.timedelta(seconds=1)
    # an order placed before the surcharge was frozen: never the live value
    assert refunds._fee_rule({}, later.valid_from - second).surcharge == D("2.00")
    assert refunds._fee_rule({}, later.valid_from + second).surcharge == D("9.00")


# --- reversal failure after the provider refund, replay of a completing refund ----------------


def test_a_failed_reversal_keeps_the_refund_pending_and_the_sweep_completes_it(
    stripe: FakeStripeClient,
) -> None:
    sub = pay(stripe, place_409())
    request_id = rid()
    stripe.fail_reversals = 1
    refund_through(sub, AMOUNT("amount", amount=D("61.00")), request_id=request_id)
    refund = Refund.objects.get()
    assert refund.reversal_pending
    assert refund.provider_refund_ref
    assert kinds(sub)["refund"] == D("-61.00")
    assert AuditLog.objects.filter(action=refunds.REFUND_REVERSAL_PENDING).exists()
    # a retry of the same request finds the Refund, never refunds again, and clears the reversal
    refund_through(sub, AMOUNT("amount", amount=D("61.00")), request_id=request_id)
    refund.refresh_from_db()
    assert not refund.reversal_pending
    assert stripe.calls.count("create_refund") == 1
    assert Refund.objects.count() == 1
    assert AuditLog.objects.filter(action=refunds.REFUND_REVERSAL_DONE).exists()


def test_the_sweep_retries_the_reversal_under_the_same_key(stripe: FakeStripeClient) -> None:
    sub = pay(stripe, place_409())
    stripe.fail_reversals = 1
    refund_through(sub, AMOUNT("amount", amount=D("61.00")))
    assert refunds.retry_pending_reversals() == 1
    assert not Refund.objects.get().reversal_pending
    assert stripe.calls.count("create_refund") == 1
    assert len(stripe.reversals) == 1  # one reversal, taken once
    assert refunds.retry_pending_reversals() == 0


def test_a_replay_of_a_refund_that_completed_the_package_returns_the_same_result(
    stripe: FakeStripeClient,
) -> None:
    sub = pay(stripe, place_409())
    request_id = rid()
    whole = AMOUNT("amount", amount=D("122.00"))
    refund_through(sub, whole, request_id=request_id)
    sub.refresh_from_db()
    assert sub.state in {"cancelled", "refunded"}
    again = refund_through(sub, whole, request_id=request_id)
    assert again.is_empty
    assert Refund.objects.count() == 1
    assert stripe.calls.count("create_refund") == 1


# --- outside the platform ---------------------------------------------------------------------


def mark(sub: VendorOrder, **kwargs: Any) -> Any:
    values: dict[str, Any] = {
        "request_id": rid(),
        "actor": member_of(sub),
        "ctx": VENDOR_CTX,
        "declared_date": timezone.localdate(),
        "declared_method": "bank transfer",
    }
    values.update(kwargs)
    return refunds.mark_refunded_outside(sub, AMOUNT("amount", amount=D("30.00")), **values)


def test_an_outside_refund_moves_nothing_and_is_audited_with_date_and_method(
    stripe: FakeStripeClient,
) -> None:
    sub = pay(stripe, place_409())
    entries = list(LedgerEntry.objects.order_by("id").values_list("pk", flat=True))
    balance = vendor_balance(sub.vendor)
    changes = mark(sub, declared_date=timezone.localdate() - dt.timedelta(days=2))
    assert list(LedgerEntry.objects.order_by("id").values_list("pk", flat=True)) == entries
    assert vendor_balance(sub.vendor) == balance
    assert stripe.calls.count("create_refund") == 0
    assert stripe.reversals == []
    refund = changes.refund
    assert refund is not None
    assert refund.channel == "outside_platform"
    assert refund.declared_method == "bank transfer"
    entry = AuditLog.objects.get(action="ledger.refund_marked_outside")
    assert entry.after is not None
    assert entry.after["declared_date"] == str(timezone.localdate() - dt.timedelta(days=2))
    assert entry.after["platform_liable"] is False
    assert entry.after["dac7_consideration_changed"] is False
    sub.order.refresh_from_db()
    assert sub.order.state == "paid"  # no state change either


def test_an_outside_refund_needs_the_date_and_the_method(stripe: FakeStripeClient) -> None:
    sub = pay(stripe, place_409())
    with pytest.raises(refunds.RefundRefused):
        mark(sub, declared_method="  ")
    with pytest.raises(refunds.RefundRefused):
        mark(sub, declared_date=timezone.localdate() + dt.timedelta(days=1))
    with pytest.raises(TypeError):
        refunds.mark_refunded_outside(  # type: ignore[call-arg]
            sub, AMOUNT("amount", amount=D("1.00")), request_id=rid(),
            actor=member_of(sub), ctx=VENDOR_CTX, declared_method="cash",
        )  # fmt: skip
    assert not Refund.objects.exists()


def test_an_outside_refund_is_refused_when_the_operator_forbids_it(
    stripe: FakeStripeClient,
) -> None:
    sub = pay(stripe, place_409())
    set_operator(vendors_may_refund_outside=False)
    with pytest.raises(refunds.RefundRefused):
        mark(sub)
    assert not Refund.objects.exists()


def test_an_outside_mark_by_a_stranger_is_refused_and_a_dry_run_writes_nothing(
    stripe: FakeStripeClient,
) -> None:
    sub = pay(stripe, place_409())
    with pytest.raises(PermissionDenied):
        mark(sub, actor=UserFactory.create())
    assert mark(sub, dry_run=True).refund is None
    assert not Refund.objects.exists()


def test_an_outside_mark_does_not_use_up_what_the_platform_can_refund(
    stripe: FakeStripeClient,
) -> None:
    """A buyer who says it was never refunded goes to the vendor-default path: the platform can
    still refund the whole order."""
    sub = pay(stripe, place_409())
    mark(sub)
    refunds.refund_by_platform(
        sub, AMOUNT("amount", amount=D("122.00")), cause="vendor_default", request_id=rid(),
        actor=UserFactory.create(staff=True), ctx=STAFF_CTX,
    )  # fmt: skip
    assert Refund.objects.filter(channel="platform_initiated").count() == 1


# --- a refund before the pending transfer ------------------------------------------------------


def test_a_partial_refund_before_the_transfer_reduces_it_and_reverses_nothing(
    stripe: FakeStripeClient,
) -> None:
    sub = pay(stripe, place_409(), transfer=False)
    refund_through(sub, AMOUNT("amount", amount=D("61.00")))
    assert stripe.reversals == []  # no transfer existed: nothing to reverse
    assert "transfer_reversal" not in kinds(sub)
    outcome = settlement.settle_pending()
    assert len(outcome.transfers) == 1
    (transfer,) = stripe.transfers.values()
    assert transfer.amount_minor == 4356  # 95.12 - 51.56 (the clawback incl. the fee)
    assert kinds(sub)["transfer"] == D("-43.56")
    assert vendor_balance(sub.vendor) == D("0.00")
    assert settlement.settle_pending().transfers == []


def test_a_full_refund_before_the_transfer_cancels_it(stripe: FakeStripeClient) -> None:
    sub = pay(stripe, place_409(), transfer=False)
    refund_through(sub, AMOUNT("amount", amount=D("122.00")))
    outcome = settlement.settle_pending()
    assert outcome.transfers == []
    assert stripe.transfers == {}
    assert stripe.reversals == []
    assert "transfer" not in kinds(sub)
    assert settlement.pending_payments() == []  # nothing is left pending for the sweep
    assert vendor_balance(sub.vendor) == D("-4.00")  # only the refund fee is owed by the vendor


# --- the duplicate PaymentIntent ---------------------------------------------------------------


def second_intent(stripe: FakeStripeClient, order: Order) -> str:
    intent = stripe.create_payment_intent(
        amount_minor=stripe_engine.to_minor(order.grand_total, "EUR"),
        currency="EUR",
        transfer_group=order.number,
        methods=["card"],
        metadata={"order_ref": order.number},
        idempotency_key=f"second-{secrets.token_hex(4)}",
    )
    return intent.id


def test_a_duplicate_payment_is_audited_alerted_refunded_in_full_and_settles_nothing(
    stripe: FakeStripeClient, caplog: pytest.LogCaptureFixture
) -> None:
    order = place_409()
    sub = pay(stripe, order)
    first_balance = vendor_balance(sub.vendor)
    entries = LedgerEntry.objects.count()
    transfers = dict(stripe.transfers)
    duplicate_ref = second_intent(stripe, order)
    with caplog.at_level(logging.ERROR, logger="mpmv.alerts"):
        assert webhook(stripe, order, duplicate_ref, "evt_dup").status_code == 200
    assert any("payments:duplicate" in record.getMessage() for record in caplog.records)
    audit = AuditLog.objects.get(action="orders.payment_duplicate")
    assert audit.after is not None
    assert audit.after["paid_with"] == duplicate_ref
    assert audit.after["expected"] == order.payment_intent_ref
    payment = Payment.objects.get(provider_ref=duplicate_ref)
    assert payment.duplicate
    assert payment.duplicate_refund_ref == ""
    (job,) = tasks.list_jobs("payments.refund_duplicate_payment", status="todo")
    assert job.task_name == "payments.refund_duplicate_payment"

    assert not duplicates.refund_duplicate_payment(payment).is_empty
    payment.refresh_from_db()
    assert payment.duplicate_refund_ref
    assert stripe._refunded[duplicate_ref] == 12200  # refunded in full
    assert stripe.reversals == []  # no vendor share reversed
    assert stripe.transfers == transfers  # no transfer from the second payment
    assert LedgerEntry.objects.count() == entries  # no ledger line, no refund fee
    assert vendor_balance(sub.vendor) == first_balance
    assert not Refund.objects.exists()
    assert duplicates.refund_duplicate_payment(payment).is_empty  # once only
    assert stripe.calls.count("create_refund") == 1
    order.refresh_from_db()
    assert order.state == "paid"


def test_a_duplicate_while_the_first_transfer_is_pending_refunds_it_and_still_pays_the_vendor(
    stripe: FakeStripeClient,
) -> None:
    order = place_409()
    sub = pay(stripe, order, transfer=False)
    duplicate_ref = second_intent(stripe, order)
    webhook(stripe, order, duplicate_ref, "evt_dup")
    duplicates.refund_pending_duplicates()  # the sweep finds it
    assert stripe._refunded[duplicate_ref] == 12200
    assert stripe.reversals == []
    outcome = settlement.settle_pending()
    assert len(outcome.transfers) == 1
    first_charge = stripe.intents[order.payment_intent_ref].charge_id
    assert {c["source_transaction"] for c in stripe.transfer_calls} == {first_charge}
    assert kinds(sub)["transfer"] == D("-95.12")  # the whole first-payment share, not reduced


def test_a_failed_duplicate_refund_is_audited_and_the_sweep_retries(
    stripe: FakeStripeClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    order = place_409()
    pay(stripe, order)
    duplicate_ref = second_intent(stripe, order)
    real = stripe.create_refund

    def down(**kwargs: Any) -> Any:
        raise StripeError("Stripe is down")

    monkeypatch.setattr(stripe, "create_refund", down)
    webhook(stripe, order, duplicate_ref, "evt_dup")
    payment = Payment.objects.get(provider_ref=duplicate_ref)
    assert duplicates.refund_duplicate_payment(payment).is_empty
    duplicates.refund_pending_duplicates()
    assert AuditLog.objects.filter(action="payments.payment.duplicate_refund_failed").count() == 1
    monkeypatch.setattr(stripe, "create_refund", real)
    assert duplicates.refund_pending_duplicates() == 1
    payment.refresh_from_db()
    assert payment.duplicate_refund_ref


def test_a_dry_run_of_the_duplicate_refund_changes_nothing(stripe: FakeStripeClient) -> None:
    order = place_409()
    pay(stripe, order)
    duplicate_ref = second_intent(stripe, order)
    webhook(stripe, order, duplicate_ref, "evt_dup")
    payment = Payment.objects.get(provider_ref=duplicate_ref)
    assert not duplicates.refund_duplicate_payment(payment, dry_run=True).is_empty
    payment.refresh_from_db()
    assert payment.duplicate_refund_ref == ""
    assert stripe.calls.count("create_refund") == 0


def test_the_orders_own_payment_redelivered_is_not_a_duplicate(stripe: FakeStripeClient) -> None:
    order = place_409()
    pay(stripe, order)
    assert webhook(stripe, order, order.payment_intent_ref, "evt_again").status_code == 200
    assert not AuditLog.objects.filter(action="orders.payment_duplicate").exists()
    assert not Payment.objects.filter(duplicate=True).exists()


def test_the_refund_service_ignores_a_duplicate_payment(stripe: FakeStripeClient) -> None:
    order = place_409()
    sub = pay(stripe, order)
    duplicate_ref = second_intent(stripe, order)
    webhook(stripe, order, duplicate_ref, "evt_dup")
    refund_through(sub, AMOUNT("amount", amount=D("10.00")))
    refund = Refund.objects.get()
    payment = Payment.objects.get(provider_ref=order.payment_intent_ref)
    assert payment.duplicate is False
    assert refund.provider_refund_ref.startswith("re_")
    assert duplicate_ref not in stripe._refunded


# --- the database refuses a refund row that breaks the rules ----------------------------------


@pytest.mark.parametrize(
    "fields",
    [
        {"declared_method": ""},  # an outside mark needs its method
        {"declared_date": None},  # and its date
        {"channel": "through_platform"},  # only an outside mark carries a declared date
        {"platform_error": True},  # the platform-error flag needs a reason
        {"refunded_gross": D("0.00")},
    ],
)
def test_the_database_refuses_a_refund_row_that_breaks_the_rules(fields: dict[str, Any]) -> None:
    from django.db import IntegrityError, transaction

    with pytest.raises(IntegrityError), transaction.atomic():
        RefundFactory.create(**fields)


def test_the_refund_row_factory_builds_a_valid_outside_mark() -> None:
    row = RefundFactory.create()
    assert row.channel == "outside_platform"
    assert row.vendor_order.vendor_id == row.vendor_id


def test_a_dry_run_of_the_state_roll_changes_no_state(stripe: FakeStripeClient) -> None:
    from apps.orders.services import apply_refund_to_states

    sub = pay(stripe, place_409())
    changes = apply_refund_to_states(sub, fully_refunded=True, ctx=CTX, dry_run=True)
    assert [c.model for c in changes.changes] == ["orders.vendororder", "orders.order"]
    sub.refresh_from_db()
    sub.order.refresh_from_db()
    assert (sub.state, sub.order.state) == ("pending", "paid")
