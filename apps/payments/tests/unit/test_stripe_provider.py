"""PAY.1: the Stripe provider passes the PAY.5 contract with a mocked client interface."""

import json
import secrets
import urllib.error
import urllib.request
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.core.checks import run_checks
from django.test import override_settings
from django.utils import timezone

from apps.payments import registry, stripe_engine
from apps.payments.engine import (
    InvalidSignature,
    PaymentProvider,
    PaymentRequest,
    PaymentStatus,
    ProviderError,
    RefundRequest,
    VendorShare,
)
from apps.payments.stripe_client import HttpStripeClient, StripeError
from apps.payments.stripe_provider import StripePaymentProvider
from apps.payments.tests.fake_stripe import FakeStripeClient
from apps.payments.tests.provider_contract import PaymentProviderContract

NOW = 1_800_000_000.0


class Harness:
    """A provider with a known webhook secret and a fixed clock."""

    def __init__(self, client: FakeStripeClient | None = None) -> None:
        self.secret = secrets.token_urlsafe(24)
        self.client = client or FakeStripeClient()
        self.provider = StripePaymentProvider(
            self.client, webhook_secret=self.secret, clock=lambda: NOW
        )


class TestStripeProviderContract(PaymentProviderContract):
    def make_provider(self) -> PaymentProvider:
        harness = Harness()
        self._harness = harness
        return harness.provider

    def signed_webhook(self, provider: PaymentProvider, event_id: str) -> tuple[bytes, str]:
        body = json.dumps({"id": event_id, "type": "payment_intent.succeeded", "data": {}}).encode()
        return body, stripe_engine.sign_payload(body, self._harness.secret, int(NOW))


def request(key: str = "k") -> PaymentRequest:
    return PaymentRequest(
        "order-9",
        "EUR",
        Decimal("100.00"),
        (VendorShare("acct_a", Decimal("60.00")), VendorShare("acct_b", Decimal("30.00"))),
        Decimal("10.00"),
        key,
    )


def test_every_transfer_is_tied_to_the_orders_charge() -> None:
    harness = Harness()
    result = harness.provider.create_payment(request())
    assert result.charge_ref
    assert {c["source_transaction"] for c in harness.client.transfer_calls} == {result.charge_ref}
    assert {t.transfer_group for t in harness.client.transfers.values()} == {"order-9"}
    amounts = sorted(t.amount_minor for t in harness.client.transfers.values())
    assert amounts == [3000, 6000]  # the 10.00 platform fee stays on the platform balance


def test_a_payment_the_customer_has_not_confirmed_stays_pending_without_transfers() -> None:
    harness = Harness(FakeStripeClient(confirm=False))
    result = harness.provider.create_payment(request())
    assert result.status is PaymentStatus.PENDING
    assert result.transfer_refs == ()
    assert harness.client.transfers == {}


def test_a_finer_amount_than_the_currency_allows_is_refused_not_rounded() -> None:
    bad = PaymentRequest(
        "o", "EUR", Decimal("10.005"), (VendorShare("a", Decimal("10.005")),), Decimal("0"), "k"
    )
    with pytest.raises(ProviderError):
        Harness().provider.create_payment(bad)


def test_refund_reverses_each_vendor_share_pro_rata_and_reports_the_cost() -> None:
    harness = Harness(FakeStripeClient(refund_fee_minor=45))
    paid = harness.provider.create_payment(request())
    refund = harness.provider.refund(
        RefundRequest(paid.provider_ref, Decimal("50.00"), "EUR", "rf-1")
    )
    assert refund.provider_cost == Decimal("0.45")
    assert sorted(minor for _, minor in harness.client.reversals) == [1500, 3000]


def test_refund_without_reversal_leaves_the_transfers() -> None:
    harness = Harness()
    paid = harness.provider.create_payment(request())
    harness.provider.refund(
        RefundRequest(paid.provider_ref, Decimal("10.00"), "EUR", "rf", reverse_vendor_shares=False)
    )
    assert harness.client.reversals == []


def test_payouts_carry_the_payment_reference() -> None:
    harness = Harness()
    paid = harness.provider.create_payment(request())
    payouts = harness.provider.list_payouts(timezone.now() - timedelta(hours=1))
    assert {p.payment_ref for p in payouts} == {paid.provider_ref}
    assert {p.vendor_account_ref for p in payouts} == {"acct_a", "acct_b"}


def test_checkout_offers_only_what_stripe_reports_and_no_operator_account() -> None:
    provider = Harness().provider
    assert tuple(provider.supported_methods()) == ("card",)
    assert provider.collects_on_operator_account is False  # D-08
    assert registry._FACTORIES["stripe"]().collects_on_operator_account is False


def test_stripe_is_the_only_registered_provider() -> None:
    with override_settings(PAYMENT_ALLOW_FAKE_PROVIDER=False):
        assert registry.registered_codes() == ["stripe"]


# --- Webhook signature -------------------------------------------------------------------------


BODY = b'{"id":"e","type":"t"}'


def _signed(secret: str, timestamp: int, body: bytes = BODY) -> tuple[bytes, str]:
    return body, stripe_engine.sign_payload(body, secret, timestamp)


def test_a_valid_signature_inside_the_tolerance_is_accepted() -> None:
    harness = Harness()
    body, header = _signed(harness.secret, int(NOW) - 299)
    assert harness.provider.parse_webhook(body, header).event_id == "e"


@pytest.mark.parametrize("offset", [301, -301, 100_000])
def test_a_stale_or_future_timestamp_is_refused(offset: int) -> None:
    harness = Harness()
    body, header = _signed(harness.secret, int(NOW) - offset)
    with pytest.raises(InvalidSignature):
        harness.provider.parse_webhook(body, header)


@pytest.mark.parametrize("header", ["", "t=abc,v1=00", "v1=00", "t=1800000000", "garbage"])
def test_malformed_headers_are_refused(header: str) -> None:
    with pytest.raises(InvalidSignature):
        Harness().provider.parse_webhook(b"{}", header)


def test_a_signature_made_with_another_secret_is_refused() -> None:
    body, header = _signed(secrets.token_urlsafe(24), int(NOW))
    with pytest.raises(InvalidSignature):
        Harness().provider.parse_webhook(body, header)


def test_no_webhook_secret_refuses_everything() -> None:
    provider = StripePaymentProvider(FakeStripeClient(), webhook_secret="", clock=lambda: NOW)
    body, header = _signed("", int(NOW))
    with pytest.raises(InvalidSignature):
        provider.parse_webhook(body, header)


def test_any_of_several_v1_signatures_may_match() -> None:
    harness = Harness()
    body, header = _signed(harness.secret, int(NOW))
    header += ",v1=" + "0" * 64
    assert harness.provider.parse_webhook(body, header).event_id == "e"


# --- Pure helpers -------------------------------------------------------------------------------


def test_minor_units() -> None:
    assert stripe_engine.to_minor(Decimal("12.34"), "EUR") == 1234
    assert stripe_engine.to_minor(Decimal("500"), "JPY") == 500
    assert stripe_engine.from_minor(1234, "EUR") == Decimal("12.34")
    with pytest.raises(ProviderError):
        stripe_engine.to_minor(Decimal("1.001"), "EUR")


def test_reversal_rounds_half_up_per_transfer_and_never_exceeds_the_transfer() -> None:
    # 1/3 of each transfer: 100 -> 33.33 -> 33, 5 -> 1.67 -> 2, 1 -> 0.33 -> 0
    assert stripe_engine.reversal_amounts([100, 5, 1], refund_minor=10, charge_minor=30) == [
        33,
        2,
        0,
    ]
    assert stripe_engine.reversal_amounts([10], refund_minor=100, charge_minor=100) == [10]


# --- HTTP client (the one place that speaks HTTP; transport is injected, no network) -----------


def _client(answer: bytes, seen: list[urllib.request.Request]) -> HttpStripeClient:
    def transport(req: urllib.request.Request) -> bytes:
        seen.append(req)
        return answer

    return HttpStripeClient(secrets.token_urlsafe(16), transport=transport)


def test_http_client_sends_form_encoded_calls_with_an_idempotency_key() -> None:
    seen: list[urllib.request.Request] = []
    answer = json.dumps(
        {"id": "pi_1", "status": "requires_payment_method", "amount": 1000, "currency": "eur"}
    ).encode()
    client = _client(answer, seen)
    intent = client.create_payment_intent(
        amount_minor=1000,
        currency="EUR",
        transfer_group="order-1",
        methods=["card"],
        metadata={"order_ref": "order-1"},
        idempotency_key="idem-1",
    )
    assert intent.id == "pi_1"
    sent = seen[0]
    assert sent.get_header("Idempotency-key") == "idem-1"
    assert sent.get_header("Authorization", "").startswith("Bearer ")
    data: Any = sent.data
    assert b"transfer_group=order-1" in data
    assert b"payment_method_types%5B%5D=card" in data
    assert b"metadata%5Border_ref%5D=order-1" in data


def test_http_client_without_a_key_refuses_before_any_request() -> None:
    seen: list[urllib.request.Request] = []
    client = HttpStripeClient("", transport=lambda r: seen.append(r) or b"{}")  # type: ignore[func-returns-value]
    with pytest.raises(StripeError):
        client.get_payment_intent("pi_1")
    assert seen == []


def test_http_client_errors_never_carry_the_key() -> None:
    key = secrets.token_urlsafe(16)

    def transport(req: urllib.request.Request) -> bytes:
        raise urllib.error.URLError("boom " + key)

    with pytest.raises(StripeError) as error:
        HttpStripeClient(key, transport=transport).get_payment_intent("pi_1")
    assert key not in str(error.value)


# --- System check -------------------------------------------------------------------------------


def _ids() -> list[str]:
    return [m.id for m in run_checks() if m.id and m.id.startswith("payments.")]


@pytest.mark.parametrize("flag", ["LIVE_DATA", "STAGE"])
def test_stripe_without_keys_is_refused_on_live_and_stage(flag: str) -> None:
    with override_settings(PAYMENT_PROVIDER="stripe", STRIPE_SECRET_KEY="", **{flag: True}):
        assert _ids() == ["payments.E003"]


def test_stripe_with_keys_passes_and_a_dev_machine_may_lack_them() -> None:
    keys = {
        "STRIPE_SECRET_KEY": secrets.token_urlsafe(16),
        "STRIPE_WEBHOOK_SECRET": secrets.token_urlsafe(16),
    }
    with override_settings(PAYMENT_PROVIDER="stripe", LIVE_DATA=True, **keys):
        assert _ids() == []
    with override_settings(PAYMENT_PROVIDER="stripe", STRIPE_SECRET_KEY=""):
        assert _ids() == []
