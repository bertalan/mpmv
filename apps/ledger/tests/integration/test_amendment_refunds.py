"""ORD.20 (2/3): amendments that move money, on real PostgreSQL with the Stripe double.

Seed vendor order (spec 07, ORD.20): A 2 x 50.00 and B 1 x 30.00 net at 22 %, shipping 10.00 net,
gross 170.80, 10 % commission rule. The rule here is on goods and shipping (14.00), so the PAY.4
pro-rata reversal and the version delta agree (3.00 for B, 1.00 for A's lower price); see the PR
for the goods-only rule, where they differ. Stripe is doubled only behind our own `StripeClient`.
Every assertion of an outcome says in plain words what was expected and what happened (owner,
2026-11-16: control tests with intelligible answers).
"""

import datetime as dt
import inspect
import re
import secrets
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts import levels
from apps.accounts.tests.factories import UserFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.core.services import FLAG_CACHE
from apps.core.tests.factories import PlatformOperatorFactory, utc
from apps.ledger import refunds, settlement
from apps.ledger.models import LedgerEntry, Refund
from apps.ledger.refund_engine import RefundSelection
from apps.ledger.tests.integration.test_refunds import SECRET, webhook
from apps.orders import amendment_engine as am
from apps.orders import amendments, services
from apps.orders.models import AmendmentState, OrderAmendment, VendorOrder
from apps.orders.services import place_order
from apps.orders.tests.control import plainly
from apps.orders.tests.integration.test_place_order import FakeTax, line, preview, vendor_of
from apps.payments import registry
from apps.payments.models import Payment
from apps.payments.stripe_provider import StripePaymentProvider, provider_from_settings
from apps.payments.tests.fake_stripe import FakeStripeClient
from apps.shipping.models import ShippingMethod
from apps.shipping.tests.factories import (
    ShippingMethodFactory,
    ShippingMethodTranslationFactory,
    ShippingZoneFactory,
    WeightBandFactory,
)
from apps.tax import engine as tax
from apps.tax.tests.factories import TaxRateFactory, VendorTaxProfileFactory
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db

D = Decimal
CTX = AuditContext(actor_label="vendor:test", source=AuditLog.Source.VENDOR)
ROME = {"line1": "1 Via Test", "city": "Roma", "postcode": "00100", "country": "IT"}
RULE = {
    "rule_id": "ord20",
    "version": 1,
    "percent": "10.00",
    "base": "net_goods_and_shipping",
    "fee_percent": "0.00",
    "fee_fixed": "0.00",
    "fee_base": "gross_paid",
    "promo_label": "",
}
DOMESTIC = FakeTax(rate=D("22"), reason=tax.REASON_DOMESTIC_VENDOR_COUNTRY)
CUSTOMER_ASKED = {"channel": "email", "date": "2026-11-16", "text": "Please fix my house number"}


@pytest.fixture
def stripe(settings: Any) -> Iterator[FakeStripeClient]:
    """The Stripe double behind our own provider (as in `test_refunds`)."""
    client = FakeStripeClient(confirm=False, refund_fee_minor=120)
    provider = StripePaymentProvider(client, webhook_secret=SECRET)
    settings.PAYMENT_PROVIDER = "stripe"
    registry.unregister_provider("stripe")
    registry.register_provider(lambda: provider)
    yield client
    registry.unregister_provider("stripe")
    registry.register_provider(provider_from_settings)


@pytest.fixture(autouse=True)
def _setup() -> Iterator[None]:
    FLAG_CACHE.clear()
    PlatformOperatorFactory.create(
        valid_from=dt.datetime(2020, 1, 1, tzinfo=dt.UTC), open_ended=True, country="IT"
    )
    TaxRateFactory.create(
        country="IT", category=tax.STANDARD, rate=D("22"), valid_from=utc(2020, 1, 1)
    )
    yield
    FLAG_CACHE.clear()


def method(seller: Vendor, cost: str, *, zone: Any = None) -> None:
    found = ShippingMethodFactory.create(
        vendor=seller,
        zone=zone or ShippingZoneFactory.create(vendor=seller, countries=["IT"]),
        key="standard",
        is_default=True,
        min_delivery_days=1,
        max_delivery_days=5,
    )
    ShippingMethodTranslationFactory.create(method=found, name="Standard", status="published")
    WeightBandFactory.create(method=found, from_kg=D("0"), to_kg=D("30"), cost_net=D(cost))


def seller() -> Vendor:
    """An Italian vendor without OSS, standard shipping 10.00 net in Italy, 5 days."""
    found = VendorFactory.create(status=Vendor.Status.ACTIVE, country="IT", sells_online=True)
    VendorTaxProfileFactory.create(vendor=found, valid_from=utc(2020, 1, 1))
    method(found, "10.00")
    return found


def place_seed(stripe: FakeStripeClient, owner: Vendor | None = None, **kw: Any) -> VendorOrder:
    """The ORD.20 seed, placed, paid through the Stripe double and transferred."""
    owner = owner or seller()
    from apps.catalog.models import Product
    from apps.catalog.tests.factories import ProductFactory

    def product(price: str) -> Product:
        return ProductFactory.create(
            vendor=owner,
            status=Product.Status.PUBLISHED,
            stock_qty=10,
            price_net=D(price),
            weight_kg=D("0.500"),
            fiscal_category=tax.STANDARD,
        )

    a, b = product("50.00"), product("30.00")
    order, _ = place_order(
        preview(
            vendor_of(
                a,
                line(a, 2, title="A", tax=DOMESTIC),
                line(b, 1, title="B", tax=DOMESTIC),
                shipping_net=D("10.00"),
                shipping_tax=DOMESTIC,
                shipping_choice={"key": "standard", "label": "Standard", "max_days": 5},
                commission_snapshot=RULE,
            ),
            shipping_address=ROME,
            billing_address=ROME,
            **kw,
        ),
        ctx=CTX,
        now=timezone.now(),
    )
    assert order is not None
    assert order.grand_total == D("170.80"), plainly(
        "the seed order must total 170.80", f"it totals {order.grand_total}"
    )
    event = f"evt_{secrets.token_hex(6)}"
    assert webhook(stripe, order, order.payment_intent_ref, event).status_code == 200
    settlement.transfer_payment(Payment.objects.get(provider_ref=order.payment_intent_ref))
    return order.vendor_orders.get()


def owner_of(sub: VendorOrder) -> Any:
    return VendorMemberFactory.create(vendor=sub.vendor, role="owner").user


def key_of(sub: VendorOrder, sku_title: str) -> str:
    return str(sub.lines.get(title=sku_title).public_id)


def draft(sub: VendorOrder, actor: Any, *changes: am.AmendmentChange, **kw: Any) -> Any:
    kw.setdefault("reason", "Out of stock at the supplier")
    found, _ = amendments.draft_amendment(sub, list(changes), actor=actor, ctx=CTX, **kw)
    assert found is not None
    return found


def confirm(amendment: OrderAmendment, actor: Any) -> str:
    """A fresh passkey grant for exactly this amendment (what the CORE.25 prompt mints)."""
    ask = amendments.amendment_passkey(amendment)
    assert ask is not None
    session = secrets.token_hex(16)
    levels.create_grant(session, actor, ask.action, ask.digest, ttl=120)
    return session


def apply(amendment: OrderAmendment, actor: Any, **kw: Any) -> Any:
    kw.setdefault("passkey_session", confirm(amendment, actor))
    return amendments.apply_amendment(amendment, actor=actor, ctx=CTX, **kw)


def rows(sub: VendorOrder, after_id: int = 0) -> list[tuple[str, Decimal]]:
    return [
        (e.kind, e.amount)
        for e in LedgerEntry.objects.filter(vendor_order=sub, pk__gt=after_id).order_by("id")
    ]


def last_id(sub: VendorOrder) -> int:
    entry = LedgerEntry.objects.filter(vendor_order=sub).order_by("-id").first()
    return entry.pk if entry else 0


def assert_balanced(sub: VendorOrder, when: str) -> None:
    """The ledger of the package sums to zero after each version: the customer's money (sale and
    refunds) equals what the platform kept and what the vendor got (commission, collection cost,
    transfer and its reversals). The refund fee is the one line outside: a debit the vendor owes
    the platform, settled against later transfers. And what the customer paid net of refunds is
    the current version's gross."""
    entries = LedgerEntry.objects.filter(vendor_order=sub)
    total = sum((e.amount for e in entries.exclude(kind="refund_fee")), D("0"))
    assert total == 0, plainly(f"the package's ledger must sum to zero {when}", f"it sums {total}")
    sub.refresh_from_db()
    assert sub.current_version is not None
    charged = sum((e.amount for e in entries.filter(kind__in=["sale", "shipping"])), D("0"))
    refunded = -sum((e.amount for e in entries.filter(kind="refund")), D("0"))
    assert charged - refunded == sub.current_version.gross_total, plainly(
        f"what the customer paid net of refunds must be the current version's gross {when}",
        f"it is {charged - refunded} against {sub.current_version.gross_total}",
    )


# --- the regression cases ----------------------------------------------------------------------


def test_removing_b_applies_v2_refunds_36_60_and_reverses_3_00_with_one_refund_fee(
    stripe: FakeStripeClient,
) -> None:
    sub = place_seed(stripe)
    assert_balanced(sub, "after v1")
    actor = owner_of(sub)
    amendment = draft(sub, actor, am.RemoveLine(key_of(sub, "B")))
    apply(amendment, actor)

    sub.refresh_from_db()
    assert getattr(sub.current_version, "number", None) == 2, plainly(
        "removing B must apply v2 at once", f"the current version is {sub.current_version}"
    )
    refund = Refund.objects.get(vendor_order=sub)
    assert refund.refunded_gross == D("36.60"), plainly(
        "removing B must refund 36.60", f"it refunded {refund.refunded_gross}"
    )
    assert refund.commission_reversal == D("3.00"), plainly(
        "removing B must give back 3.00 commission", f"it gave back {refund.commission_reversal}"
    )
    fees = LedgerEntry.objects.filter(vendor_order=sub, kind="refund_fee")
    assert (fees.count(), refund.refund_fee > 0) == (1, True), plainly(
        "a removal the vendor makes must write one refund fee", f"{fees.count()} fee line(s)"
    )
    assert refund.amendment_id == amendment.pk
    assert refund.fee_exclusion_reason == ""
    amendment.refresh_from_db()
    assert amendment.state == AmendmentState.APPLIED
    assert amendment.refund_ref == str(refund.public_id)
    assert stripe.calls.count("create_refund") == 1, plainly(
        "the refund must be submitted to the provider in the same step",
        f"the provider saw {stripe.calls.count('create_refund')} refund call(s)",
    )
    assert_balanced(sub, "after v2")


def test_the_removal_writes_the_same_ledger_rows_as_a_pay7_refund_of_line_b(
    stripe: FakeStripeClient,
) -> None:
    amended = place_seed(stripe)
    refunded = place_seed(stripe)
    actor = owner_of(amended)
    start_a, start_b = last_id(amended), last_id(refunded)
    apply(draft(amended, actor, am.RemoveLine(key_of(amended, "B"))), actor)
    refunds.refund_through_platform(
        refunded,
        RefundSelection("line", ((refunded.lines.get(title="B").pk, 1),)),
        request_id=secrets.token_urlsafe(12),
        actor=VendorMemberFactory.create(vendor=refunded.vendor, role="staff").user,
        ctx=CTX,
    )
    assert rows(amended, start_a) == rows(refunded, start_b), plainly(
        "removing B by amendment must write the same ledger rows as a PAY.7 refund of line B",
        f"the amendment wrote {rows(amended, start_a)} and the refund {rows(refunded, start_b)}",
    )


def test_lowering_a_to_45_refunds_12_20_and_reverses_1_00(stripe: FakeStripeClient) -> None:
    sub = place_seed(stripe)
    actor = owner_of(sub)
    apply(draft(sub, actor, am.ChangePrice(key_of(sub, "A"), D("45.00"))), actor)
    refund = Refund.objects.get(vendor_order=sub)
    assert (refund.refunded_gross, refund.commission_reversal) == (D("12.20"), D("1.00")), plainly(
        "lowering A to 45.00 must refund 12.20 and give back 1.00 commission",
        f"it refunded {refund.refunded_gross} and gave back {refund.commission_reversal}",
    )
    assert_balanced(sub, "after the lower price")


def test_a_second_version_keeps_the_ledger_at_zero(stripe: FakeStripeClient) -> None:
    sub = place_seed(stripe)
    actor = owner_of(sub)
    apply(draft(sub, actor, am.RemoveLine(key_of(sub, "B"))), actor)
    sub.refresh_from_db()
    apply(draft(sub, actor, am.ChangePrice(key_of(sub, "A"), D("45.00"))), actor)
    sub.refresh_from_db()
    assert sub.current_version is not None
    assert sub.current_version.number == 3
    assert_balanced(sub, "after v3")


# --- refund-fee exemption (owner, 2026-11-16) --------------------------------------------------


def milan_seller() -> Vendor:
    """Shipping 10.00 in Italy, 6.00 to Milan postcodes (a cheaper zone of the same territory)."""
    found = seller()
    milan = ShippingZoneFactory.create(
        vendor=found, countries=["IT"], postcode_patterns=["20*"], priority=10
    )
    method(found, "6.00", zone=milan)
    assert ShippingMethod.objects.filter(vendor=found).count() == 2
    return found


def fix_to_milan() -> am.ChangeAddress:
    return am.ChangeAddress(
        address={"line1": "3 Via Nuova", "city": "Milano", "postcode": "20121", "country": "IT"},
        requested_by_customer=True,
    )


def test_a_customer_requested_correction_refunding_shipping_charges_no_refund_fee(
    stripe: FakeStripeClient,
) -> None:
    sub = place_seed(stripe, milan_seller())
    actor = owner_of(sub)
    amendment = draft(sub, actor, fix_to_milan(), customer_request=CUSTOMER_ASKED)
    assert amendment.kind == "minor", plainly(
        "an address fix asked by the customer, same territory, cheaper shipping, must be minor",
        f"it was {amendment.kind}: {amendment.reasons}",
    )
    apply(amendment, actor)
    refund = Refund.objects.get(vendor_order=sub)
    assert refund.refunded_gross == D("4.88"), plainly(
        "the cheaper shipping must be refunded with its VAT: 4.00 + 0.88 = 4.88",
        f"{refund.refunded_gross} was refunded",
    )
    assert refund.fee_exclusion_reason == "customer_requested_amendment", plainly(
        "a refund from an address correction the customer asked for must carry the exclusion "
        "reason customer_requested_amendment",
        f"it carries {refund.fee_exclusion_reason!r}",
    )
    fee_lines = LedgerEntry.objects.filter(vendor_order=sub, kind="refund_fee")
    assert (refund.refund_fee, fee_lines.exists()) == (0, False), plainly(
        "a refund from an address correction the customer asked for must not charge a refund fee",
        f"a fee of {refund.refund_fee} was written ({fee_lines.count()} line(s))",
    )
    excluded = AuditLog.objects.get(action="ledger.refund_fee_excluded")
    assert (excluded.after or {})["reason"] == "customer_requested_amendment"
    assert (excluded.after or {})["amendment"] == str(amendment.public_id)
    assert_balanced(sub, "after the address correction")


def test_a_customer_request_attached_to_a_vendor_removal_keeps_the_refund_fee(
    stripe: FakeStripeClient,
) -> None:
    sub = place_seed(stripe)
    actor = owner_of(sub)
    amendment = draft(sub, actor, am.RemoveLine(key_of(sub, "B")), customer_request=CUSTOMER_ASKED)
    apply(amendment, actor)
    refund = Refund.objects.get(vendor_order=sub)
    assert (refund.fee_exclusion_reason, refund.refund_fee > 0) == ("", True), plainly(
        "only an address correction the customer asked for is exempt; a line the vendor removes "
        "keeps the refund fee even with a customer request attached",
        f"the exclusion is {refund.fee_exclusion_reason!r} and the fee {refund.refund_fee}",
    )


def test_the_exclusion_reason_cannot_be_set_by_hand(stripe: FakeStripeClient) -> None:
    for service in (refunds.refund_through_platform, refunds.refund_by_platform):
        names = set(inspect.signature(service).parameters)
        assert not {"fee_exclusion_reason", "amendment", "exclusion"} & names, plainly(
            f"{service.__name__} must not let a person set the amendment exclusion",
            f"it takes {sorted(names)}",
        )
    sub = place_seed(stripe)
    with pytest.raises(IntegrityError), transaction.atomic():
        Refund.objects.create(
            vendor=sub.vendor,
            vendor_order=sub,
            channel="through_platform",
            idempotency_key=secrets.token_hex(8),
            refunded_gross=D("1.00"),
            fee_exclusion_reason="customer_requested_amendment",
        )


# --- the passkey (CORE.25) ---------------------------------------------------------------------


def test_a_refund_asks_a_fresh_passkey_even_with_the_window_open(
    stripe: FakeStripeClient,
) -> None:
    sub = place_seed(stripe)
    actor = owner_of(sub)
    amendment = draft(sub, actor, am.RemoveLine(key_of(sub, "B")))
    ask = amendments.amendment_passkey(amendment)
    assert getattr(ask, "action", None) == "vendor_refund", plainly(
        "a vendor amendment that refunds must spend the PAY.7 vendor_refund grant",
        f"it asks {ask}",
    )
    # An open window is a session flag; the service never reads it for a critical action.
    window = secrets.token_hex(16)
    levels.create_grant(window, actor, "export", "anything", ttl=900)
    with pytest.raises(amendments.PasskeyRequired):
        amendments.apply_amendment(amendment, actor=actor, ctx=CTX, passkey_session=window)
    assert not Refund.objects.filter(vendor_order=sub).exists()
    assert stripe.calls.count("create_refund") == 0
    amendment.refresh_from_db()
    assert amendment.state == AmendmentState.DRAFT


def test_a_grant_is_void_after_a_draft_edit_and_is_spent_once(stripe: FakeStripeClient) -> None:
    sub = place_seed(stripe)
    actor = owner_of(sub)
    amendment = draft(sub, actor, am.RemoveLine(key_of(sub, "B")))
    session = confirm(amendment, actor)
    amendments.revise_amendment(
        amendment, [am.ChangePrice(key_of(sub, "A"), D("45.00"))], actor=actor, ctx=CTX
    )
    amendment.refresh_from_db()
    assert amendment.revision == 2
    with pytest.raises(amendments.PasskeyRequired):
        amendments.apply_amendment(amendment, actor=actor, ctx=CTX, passkey_session=session)
    fresh = confirm(amendment, actor)
    amendments.apply_amendment(amendment, actor=actor, ctx=CTX, passkey_session=fresh)
    assert Refund.objects.get(vendor_order=sub).refunded_gross == D("12.20")
    assert AuditLog.objects.filter(action="orders.amendment_edited").count() == 1


def test_a_grant_for_one_amendment_does_not_apply_another(stripe: FakeStripeClient) -> None:
    sub = place_seed(stripe)
    other = place_seed(stripe, sub.vendor)
    actor = owner_of(sub)
    mine = draft(sub, actor, am.RemoveLine(key_of(sub, "B")))
    theirs = draft(other, actor, am.RemoveLine(key_of(other, "B")))
    session = confirm(mine, actor)
    with pytest.raises(amendments.PasskeyRequired):
        amendments.apply_amendment(theirs, actor=actor, ctx=CTX, passkey_session=session)


# --- the 14-day deadline and the staff alert ---------------------------------------------------


def test_an_amendment_refund_is_due_within_14_days_and_alerts_staff_4_days_before(
    stripe: FakeStripeClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sub = place_seed(stripe)
    actor = owner_of(sub)
    stripe.refund_status = "pending"
    moment = timezone.now()
    apply(draft(sub, actor, am.RemoveLine(key_of(sub, "B"))), actor, now=moment)
    refund = Refund.objects.get(vendor_order=sub)
    assert refund.due_by == moment + dt.timedelta(days=14), plainly(
        "an amendment refund must be due 14 days after the event", f"it is due {refund.due_by}"
    )
    assert refund.provider_status == "pending"
    assert refund.succeeded_at is None
    alerts: list[str] = []
    monkeypatch.setattr(refunds, "raise_alert", lambda alert, **kw: alerts.append(alert.key))
    assert refunds.alert_refunds_near_due(now=moment + dt.timedelta(days=9, hours=23)) == 0, (
        plainly("no alert may fire earlier than 4 days before the deadline", f"alerts: {alerts}")
    )
    assert refunds.alert_refunds_near_due(now=moment + dt.timedelta(days=10)) == 1, plainly(
        "a refund not succeeded 4 days before its deadline must alert staff", "none fired"
    )
    assert alerts == [f"refund-due:{refund.public_id}"]


def test_a_succeeded_refund_never_alerts(stripe: FakeStripeClient) -> None:
    sub = place_seed(stripe)
    actor = owner_of(sub)
    apply(draft(sub, actor, am.RemoveLine(key_of(sub, "B"))), actor)
    refund = Refund.objects.get(vendor_order=sub)
    assert refund.succeeded_at is not None
    assert refunds.alert_refunds_near_due(now=timezone.now() + dt.timedelta(days=20)) == 0


def test_no_refund_code_states_a_30_day_term() -> None:
    assert refunds.REFUND_DUE_DAYS == 14
    root = Path(__file__).resolve().parents[4]
    for name in (
        "apps/ledger/refunds.py",
        "apps/ledger/models.py",
        "apps/orders/amendments.py",
        "apps/orders/amendment_engine.py",
    ):
        text = (root / name).read_text()
        assert not re.search(r"\b30[ -]?(day|giorni|Tage|jours|días)", text), plainly(
            f"{name} must never state a 30-day refund term", "it does"
        )


# --- dry run, audit, failures ------------------------------------------------------------------


def test_the_dry_run_moves_nothing(stripe: FakeStripeClient) -> None:
    sub = place_seed(stripe)
    actor = owner_of(sub)
    amendment = draft(sub, actor, am.RemoveLine(key_of(sub, "B")))
    before = rows(sub)
    changes = amendments.apply_amendment(amendment, actor=actor, ctx=CTX, dry_run=True)
    estimate = [c for c in changes.changes if c.model == "ledger.refund"]
    assert estimate
    assert estimate[0].after is not None
    assert estimate[0].after["refunded_gross"] == D("36.60")
    assert estimate[0].after["estimate"] is True
    assert rows(sub) == before, plainly("a dry run must write no ledger line", "it did")
    assert not Refund.objects.filter(vendor_order=sub).exists()
    assert stripe.calls.count("create_refund") == 0, plainly(
        "a dry run must not call the provider", f"{stripe.calls.count('create_refund')} call(s)"
    )
    amendment.refresh_from_db()
    sub.refresh_from_db()
    assert amendment.state == AmendmentState.DRAFT
    assert sub.current_version is not None
    assert sub.current_version.number == 1
    assert sub.ship_hold_until is None


def test_every_step_is_audited_with_before_after_role_and_money(
    stripe: FakeStripeClient,
) -> None:
    sub = place_seed(stripe)
    actor = owner_of(sub)
    amendment = draft(sub, actor, am.RemoveLine(key_of(sub, "B")))
    apply(amendment, actor)
    drafted = AuditLog.objects.get(action="orders.amendment_drafted")
    assert (drafted.after or {})["initiator"] == "vendor_owner"
    assert (drafted.after or {})["refund"] == "36.60"
    applied = AuditLog.objects.get(
        action="orders.amendment_applied", target_model="orders.orderamendment"
    )
    assert ((applied.before or {})["state"], (applied.after or {})["state"]) == ("draft", "applied")
    assert (applied.after or {})["initiator"] == "vendor_owner"
    assert ((applied.after or {})["money_delta"], (applied.after or {})["refund"]) == (
        "-36.60",
        "36.60",
    )
    assert (applied.after or {})["refund_ref"] == str(Refund.objects.get().public_id)
    assert (applied.before or {})["to_digest"]
    assert (applied.after or {})["from_digest"]
    assert AuditLog.objects.filter(
        action="orders.amendment_applied", target_model="orders.vendororderversion"
    ).exists()
    recorded = AuditLog.objects.get(action="ledger.refund_recorded")
    assert (recorded.after or {})["amendment"] == str(amendment.public_id)
    assert (recorded.after or {})["refunded_gross"] == "36.60"


def test_a_provider_failure_rolls_everything_back(stripe: FakeStripeClient) -> None:
    sub = place_seed(stripe)
    actor = owner_of(sub)
    amendment = draft(sub, actor, am.RemoveLine(key_of(sub, "B")))
    stripe.intents.clear()  # Stripe no longer knows the payment: the refund call fails
    with pytest.raises(amendments.AmendmentRefused, match="payment provider"):
        apply(amendment, actor)
    amendment.refresh_from_db()
    sub.refresh_from_db()
    assert amendment.state == AmendmentState.DRAFT, plainly(
        "a refund the provider refused must leave the draft as it was",
        f"the amendment is {amendment.state}",
    )
    assert sub.current_version is not None
    assert sub.current_version.number == 1
    assert not Refund.objects.filter(vendor_order=sub).exists()


# --- an open amendment and other refunds -------------------------------------------------------


def test_a_refund_supersedes_an_open_amendment(stripe: FakeStripeClient) -> None:
    sub = place_seed(stripe)
    actor = owner_of(sub)
    amendment = draft(sub, actor, am.RemoveLine(key_of(sub, "B")))
    refunds.refund_through_platform(
        sub,
        RefundSelection("amount", amount=D("5.00")),
        request_id=secrets.token_urlsafe(12),
        actor=actor,
        ctx=CTX,
    )
    amendment.refresh_from_db()
    assert amendment.state == AmendmentState.SUPERSEDED, plainly(
        "a refund recorded while an amendment is open must supersede it",
        f"the amendment is {amendment.state}",
    )
    entry = AuditLog.objects.get(action="orders.amendment_superseded")
    assert (entry.after or {})["closed_by"] == "refund"


def test_an_amendment_cannot_refund_a_line_already_refunded(stripe: FakeStripeClient) -> None:
    sub = place_seed(stripe)
    actor = owner_of(sub)
    amendment = draft(sub, actor, am.RemoveLine(key_of(sub, "B")))
    session = confirm(amendment, actor)
    # The line is refunded through PAY.7 while the draft waits (which supersedes it) ...
    refunds.refund_through_platform(
        sub,
        RefundSelection("line", ((sub.lines.get(title="B").pk, 1),)),
        request_id=secrets.token_urlsafe(12),
        actor=actor,
        ctx=CTX,
    )
    with pytest.raises(amendments.AmendmentRefused):
        amendments.apply_amendment(amendment, actor=actor, ctx=CTX, passkey_session=session)
    # ... and a new draft removing it again cannot refund it twice.
    again = draft(sub, actor, am.RemoveLine(key_of(sub, "B")))
    with pytest.raises(amendments.AmendmentRefused, match="refund the customer twice"):
        apply(again, actor)
    assert Refund.objects.filter(vendor_order=sub).count() == 1


# --- cancel the rest after a partial unavailability --------------------------------------------


def test_a_removal_holds_shipping_and_offers_to_cancel_the_rest(stripe: FakeStripeClient) -> None:
    sub = place_seed(stripe)
    actor = owner_of(sub)
    moment = timezone.now()
    apply(draft(sub, actor, am.RemoveLine(key_of(sub, "B"))), actor, now=moment)
    sub.refresh_from_db()
    assert sub.ship_hold_until == moment + dt.timedelta(hours=24), plainly(
        "after a partial unavailability shipping must be held for 24 hours",
        f"the hold ends at {sub.ship_hold_until}",
    )
    services.transition_vendor_order(sub, "accepted", ctx=CTX)
    with pytest.raises(services.ShipmentOnHold, match="cancel the rest"):
        services.transition_vendor_order(
            sub, "shipped", ctx=CTX, tracking_number="T1", carrier="X", now=moment
        )
    services.transition_vendor_order(
        sub,
        "shipped",
        ctx=CTX,
        tracking_number="T1",
        carrier="X",
        now=moment + dt.timedelta(hours=24, minutes=1),
    )
    sub.refresh_from_db()
    assert sub.state == "shipped"


def test_a_lower_price_makes_no_cancel_the_rest_offer(stripe: FakeStripeClient) -> None:
    sub = place_seed(stripe)
    actor = owner_of(sub)
    apply(draft(sub, actor, am.ChangePrice(key_of(sub, "A"), D("45.00"))), actor)
    sub.refresh_from_db()
    assert sub.ship_hold_until is None
    assert sub.cancel_rest_offered_at is None


def test_the_customer_cancels_the_rest_with_a_full_refund_and_the_vendors_fee(
    stripe: FakeStripeClient,
) -> None:
    customer = UserFactory.create()
    sub = place_seed(stripe, customer=customer)
    actor = owner_of(sub)
    moment = timezone.now()
    apply(draft(sub, actor, am.RemoveLine(key_of(sub, "B"))), actor, now=moment)
    with pytest.raises(PermissionDenied):
        amendments.cancel_rest_of_package(sub, actor=UserFactory.create(), ctx=CTX)
    amendments.cancel_rest_of_package(sub, actor=customer, ctx=CTX, now=moment)
    rest = Refund.objects.get(vendor_order=sub, cause="cancel_rest")
    assert rest.refunded_gross == D("134.20"), plainly(
        "cancelling the rest must refund what is left: 170.80 - 36.60 = 134.20",
        f"{rest.refunded_gross} was refunded",
    )
    assert (rest.refund_fee > 0, rest.fee_exclusion_reason) == (True, ""), plainly(
        "a cancel of the rest keeps the vendor's refund fee", f"the fee is {rest.refund_fee}"
    )
    assert rest.due_by == moment + dt.timedelta(days=14)
    sub.refresh_from_db()
    assert sub.state == "cancelled"
    assert sub.ship_hold_until is None
    assert AuditLog.objects.filter(action="orders.amendment_package_cancelled").exists()


def test_the_customer_keeps_the_rest_and_the_hold_ends(stripe: FakeStripeClient) -> None:
    customer = UserFactory.create()
    sub = place_seed(stripe, customer=customer)
    actor = owner_of(sub)
    moment = timezone.now()
    apply(draft(sub, actor, am.RemoveLine(key_of(sub, "B"))), actor, now=moment)
    amendments.keep_rest_of_package(sub, actor=customer, ctx=CTX)
    services.transition_vendor_order(sub, "accepted", ctx=CTX)
    services.transition_vendor_order(
        sub, "shipped", ctx=CTX, tracking_number="T1", carrier="X", now=moment
    )
    sub.refresh_from_db()
    assert sub.state == "shipped"
    with pytest.raises(amendments.AmendmentRefused):
        amendments.cancel_rest_of_package(sub, actor=customer, ctx=CTX)
