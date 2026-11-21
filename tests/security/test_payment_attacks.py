"""SEC.5 attack scenarios, category "Business logic", payments and refunds (item 10, part 3a-2).

Each test plays the attacker against the real payment and refund paths (`apps.payments`,
`apps.orders`, `apps.ledger`) and asserts that the attack fails and leaves nothing behind. Stripe
is doubled only behind our own `StripeClient` interface (`FakeStripeClient`). The order is the
#409 one: a vendor order of 122.00 (see `apps/ledger/tests/integration/test_refunds.py`).

Left out on purpose, with the test that already proves it:

- a replayed event id, a second event for a recorded payment, a forged signature:
  `apps/payments/tests/integration/test_payment_webhook.py`;
- a capture of another amount or of another payment reference does not pay the order:
  `apps/payments/tests/integration/test_order_payment.py::test_a_payment_of_another_amount_does_not_pay_the_order`
  and `::test_a_payment_with_another_reference_does_not_mark_the_order_paid`;
- one refund above the capture, a stranger, a refund of an unpaid order, a repeated request id:
  `apps/ledger/tests/integration/test_refunds.py` (`test_more_than_is_left_...`,
  `test_an_unpaid_order_is_refused`, `test_a_repeat_with_the_same_request_id_refunds_once`);
- a platform refund without a reason or by a non-staff user:
  `apps/ledger/tests/integration/test_refunds.py::test_the_platform_error_flag_needs_a_reason_and_staff`;
- an amendment refund applied without the passkey, or with a grant made before an edit:
  `apps/manager/tests/integration/test_amendment_views.py`;
- the critical action floor an operator cannot lower: `apps/core/tests/unit/test_session_policy.py`.
"""

import dataclasses
import json
import secrets
import time
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.urls import reverse

from apps.accounts.tests.factories import UserFactory
from apps.catalog.models import Product
from apps.core.models import AuditLog
from apps.ledger import refunds
from apps.ledger.models import LedgerEntry, Refund
from apps.ledger.tests.integration.test_amendment_refunds import _setup  # noqa: F401 (autouse)
from apps.ledger.tests.integration.test_refunds import (
    AMOUNT,
    SECRET,
    STAFF_CTX,
    member_of,
    pay,
    place_409,
    refund_through,
    rid,
    webhook,
)
from apps.manager.tests.integration.test_amendment_views import (
    amend_url,
    draft_of,
    grant,
    member,
    remove_b,
    seeded,  # noqa: F401 (fixture)
    shop_login,
)
from apps.orders import amendments
from apps.orders import services as orders
from apps.orders.models import Order, OrderLine, VendorOrder
from apps.orders.tests.integration.test_place_order import CTX
from apps.payments import registry, stripe_engine
from apps.payments.models import Payment
from apps.payments.stripe_client import StripeError
from apps.payments.stripe_provider import StripePaymentProvider, provider_from_settings
from apps.payments.tests.fake_stripe import FakeStripeClient
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory
from tests.security.test_business_logic_attacks import _race

pytestmark = pytest.mark.django_db

D = Decimal


@pytest.fixture
def stripe(settings: Any) -> Iterator[FakeStripeClient]:
    """The Stripe double of `test_refunds`, behind our own provider."""
    client = FakeStripeClient(confirm=False, refund_fee_minor=120)
    provider = StripePaymentProvider(client, webhook_secret=SECRET)
    settings.PAYMENT_PROVIDER = "stripe"
    registry.unregister_provider("stripe")
    registry.register_provider(lambda: provider)
    yield client
    registry.unregister_provider("stripe")
    registry.register_provider(provider_from_settings)


def _signed(event: dict[str, Any]) -> int:
    body = json.dumps(event).encode()
    header = stripe_engine.sign_payload(body, SECRET, int(time.time()))
    response = Client().post(
        reverse("payments_webhook"),
        data=body,
        content_type="application/json",
        headers={"Stripe-Signature": header},
    )
    return int(response.status_code)


def _capture(order: Order, *, minor: int | None = None, currency: str = "eur") -> int:
    """A signed capture of the order's own payment, with the amount or currency the caller picks."""
    amount = stripe_engine.to_minor(order.grand_total, "EUR") if minor is None else minor
    return _signed(
        {
            "id": f"evt_{secrets.token_hex(6)}",
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": order.payment_intent_ref,
                    "amount": amount,
                    "amount_received": amount,
                    "currency": currency,
                    "metadata": {"order_ref": order.number},
                }
            },
        }
    )


def _ledger(sub: VendorOrder) -> list[tuple[str, Decimal]]:
    return sorted((e.kind, e.amount) for e in LedgerEntry.objects.filter(vendor_order=sub))


# --- webhooks: replay, mismatch, order of events ----------------------------------------------


def test_a_second_capture_event_writes_no_second_payment_ledger_line_or_transfer(
    stripe: FakeStripeClient,
) -> None:
    order = place_409()
    sub = pay(stripe, order)
    lines, transfers = _ledger(sub), dict(stripe.transfers)
    assert lines
    assert webhook(stripe, order, order.payment_intent_ref, "evt_pay").status_code == 200
    assert webhook(stripe, order, order.payment_intent_ref, "evt_again").status_code == 200
    assert Payment.objects.count() == 1
    assert _ledger(sub) == lines
    assert stripe.transfers == transfers


@pytest.mark.parametrize(("minor", "currency"), [(12200, "usd"), (12199, "eur"), (1, "eur")])
def test_a_capture_of_another_amount_or_currency_pays_nothing_and_cannot_be_refunded(
    stripe: FakeStripeClient, minor: int, currency: str
) -> None:
    order = place_409()
    assert _capture(order, minor=minor, currency=currency) == 200
    order.refresh_from_db()
    sub = order.vendor_orders.get()
    assert order.state == "awaiting_payment"
    assert AuditLog.objects.filter(action="orders.payment_mismatch").count() == 1
    assert not LedgerEntry.objects.filter(vendor_order=sub).exists()
    with pytest.raises(refunds.RefundRefused):
        refund_through(sub, AMOUNT("amount", amount=D("1.00")))
    assert stripe.calls.count("create_refund") == 0


def test_a_refund_event_before_the_capture_refunds_nothing(stripe: FakeStripeClient) -> None:
    order = place_409()
    refunded = {
        "id": f"evt_{secrets.token_hex(6)}",
        "type": "charge.refunded",
        "data": {
            "object": {
                "id": "ch_unknown",
                "payment_intent": order.payment_intent_ref,
                "amount_refunded": 12200,
                "currency": "eur",
                "metadata": {"order_ref": order.number},
            }
        },
    }
    assert _signed(refunded) == 200
    sub = pay(stripe, order)
    order.refresh_from_db()
    assert order.state == "paid"
    assert not Refund.objects.exists()
    assert "refund" not in dict(_ledger(sub))


def test_a_failure_event_after_the_capture_does_not_unpay_the_order(
    stripe: FakeStripeClient,
) -> None:
    order = place_409()
    sub = pay(stripe, order)
    lines = _ledger(sub)
    intent = {"id": order.payment_intent_ref, "metadata": {"order_ref": order.number}}
    failed = {
        "id": f"evt_{secrets.token_hex(6)}",
        "type": "payment_intent.payment_failed",
        "data": {"object": intent},
    }
    assert _signed(failed) == 200
    order.refresh_from_db()
    assert order.state == "paid"
    assert _ledger(sub) == lines


# --- refunds: amounts, races, tenancy ---------------------------------------------------------


def test_partial_refunds_cannot_add_up_beyond_the_capture(stripe: FakeStripeClient) -> None:
    sub = pay(stripe, place_409())
    refund_through(sub, AMOUNT("amount", amount=D("61.00")))
    with pytest.raises(refunds.RefundRefused):
        refund_through(sub, AMOUNT("amount", amount=D("61.01")))
    refund_through(sub, AMOUNT("amount", amount=D("61.00")))
    with pytest.raises(refunds.RefundRefused):
        refund_through(sub, AMOUNT("amount", amount=D("0.01")))
    assert sum(r.refunded_gross for r in Refund.objects.all()) == D("122.00")
    assert stripe.calls.count("create_refund") == 2


@pytest.mark.django_db(transaction=True)
def test_two_refunds_at_once_cannot_exceed_the_capture(stripe: FakeStripeClient) -> None:
    """Two refunds of 100.00 of a 122.00 capture, started at the same moment: the order lock
    serialises them, so one is refused and the provider is asked once."""
    sub = pay(stripe, place_409())
    actor = member_of(sub)

    def hundred() -> object:
        return refund_through(sub, AMOUNT("amount", amount=D("100.00")), actor=actor)

    outcomes = _race(hundred, hundred)
    assert sum(isinstance(o, refunds.RefundRefused) for o in outcomes) == 1, outcomes
    assert [r.refunded_gross for r in Refund.objects.all()] == [D("100.00")]
    assert stripe.calls.count("create_refund") == 1


@pytest.mark.django_db(transaction=True)
def test_the_same_refund_sent_twice_at_once_refunds_once(stripe: FakeStripeClient) -> None:
    sub = pay(stripe, place_409())
    actor, request_id = member_of(sub), rid()

    def again() -> object:
        selection = AMOUNT("amount", amount=D("50.00"))
        return refund_through(sub, selection, actor=actor, request_id=request_id)

    outcomes = _race(again, again)
    assert not [o for o in outcomes if isinstance(o, Exception)], outcomes
    assert Refund.objects.count() == 1
    assert stripe.calls.count("create_refund") == 1


def test_only_an_active_member_of_the_order_s_vendor_refunds_it(stripe: FakeStripeClient) -> None:
    sub = pay(stripe, place_409())
    outsider = VendorMemberFactory.create(vendor=VendorFactory.create(), role="owner").user
    leaver = VendorMemberFactory.create(vendor=sub.vendor, role="owner").user
    leaver.is_active = False
    leaver.save(update_fields=["is_active"])
    staff = UserFactory.create(is_staff=True, is_superuser=True)  # staff use the platform path
    for actor in (outsider, leaver, staff):
        with pytest.raises(PermissionDenied):
            refund_through(sub, AMOUNT("amount", amount=D("1.00")), actor=actor)
    for actor in (member_of(sub), UserFactory.create(is_staff=True, is_active=False)):
        with pytest.raises(refunds.StaffRequired):
            refunds.refund_by_platform(
                sub, AMOUNT("amount", amount=D("1.00")), cause="vendor_default",
                request_id=rid(), actor=actor, ctx=STAFF_CTX,
            )  # fmt: skip
    assert not Refund.objects.exists()
    assert stripe.calls.count("create_refund") == 0


def test_a_refund_grant_from_another_session_or_action_does_not_confirm_it(
    client: Client,
    seeded: VendorOrder,  # noqa: F811
) -> None:
    owner = member(seeded)
    shop_login(client, owner)
    remove_b(client, seeded)
    amendment = draft_of(seeded)
    apply_url = f"{amend_url(seeded)}{amendment.public_id}/apply/"
    elsewhere = Client()
    shop_login(elsewhere, owner)
    grant(elsewhere, owner, amendment)  # a grant minted in another browser session
    assert client.post(apply_url).status_code == 403
    ask = amendments.amendment_passkey(amendment)
    assert ask is not None
    other_action = dataclasses.replace(ask, action="order_amend_address")
    grant(client, owner, other_action)  # the right digest, another action
    assert client.post(apply_url).status_code == 403
    assert not Refund.objects.filter(amendment=amendment).exists()
    grant(client, owner, amendment)
    assert client.post(apply_url).status_code == 302  # the real grant confirms it
    assert Refund.objects.filter(amendment=amendment).count() == 1


# --- payment start: amount and state --------------------------------------------------------


def test_the_payment_amount_is_the_order_s_total_even_after_the_price_changes(
    stripe: FakeStripeClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = stripe.create_payment_intent

    def down(**kwargs: Any) -> Any:
        raise StripeError("down")

    monkeypatch.setattr(stripe, "create_payment_intent", down)
    order = place_409()
    assert order.payment_intent_ref == ""
    product_ids = OrderLine.objects.filter(vendor_order__order=order).values("product_id")
    Product.objects.filter(pk__in=product_ids).update(price_net=D("0.01"))
    monkeypatch.setattr(stripe, "create_payment_intent", real)
    assert orders.start_payment(order, ctx=CTX)
    [intent] = stripe.intents.values()
    assert (intent.amount_minor, intent.currency) == (12200, "EUR")


def test_a_cancelled_order_starts_no_payment_and_a_late_capture_moves_no_money(
    stripe: FakeStripeClient,
) -> None:
    order = place_409()
    orders.transition_order(order, "cancelled", ctx=CTX)
    Order.objects.filter(pk=order.pk).update(payment_intent_ref="")
    assert orders.start_payment(order, ctx=CTX) == ""
    assert stripe.calls.count("create_payment_intent") == 1  # only the one made at placing
    Order.objects.filter(pk=order.pk).update(payment_intent_ref=next(iter(stripe.intents)))
    order.refresh_from_db()
    assert _capture(order) == 200
    order.refresh_from_db()
    sub = order.vendor_orders.get()
    assert order.state == "cancelled"
    assert AuditLog.objects.filter(action="orders.payment_late").count() == 1
    assert not LedgerEntry.objects.filter(vendor_order=sub).exists()
    assert not stripe.transfers
