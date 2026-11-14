"""PAY.3 follow-up: the STAGE incident of 2026-11-14 (a transfer stuck on a cached Stripe key).

The sweep first sent a vendor transfer to a demo payout account; Stripe refused it and stored the
answer under `transfer:<order>:<vendor>`. Once the vendor was linked to a real account, every
retry with that key got `idempotency_error` back, and the audit only said "HTTP 400". The rules:
the key names destination, amount and currency; a demo account never reaches Stripe; a transfer
already made to another account is never doubled; Stripe's error type and code are kept.
"""

import io
import json
import secrets
import urllib.error
import urllib.request
from decimal import Decimal
from email.message import Message
from typing import Any

import pytest

from apps.payments import stripe_engine
from apps.payments.engine import (
    PaymentRequest,
    ProviderError,
    RefundRequest,
    TransferConflict,
    TransferRequest,
    VendorShare,
)
from apps.payments.fake import FakePaymentProvider
from apps.payments.stripe_client import HttpStripeClient, StripeError
from apps.payments.stripe_provider import StripePaymentProvider
from apps.payments.tests.fake_stripe import FakeStripeClient

ORDER = "6f1c1d1e-0000-4000-8000-000000000001"
VENDOR = "6f1c1d1e-0000-4000-8000-000000000002"


def key(destination: str = "acct_real_1", amount: str = "27.82", currency: str = "EUR") -> str:
    return stripe_engine.transfer_idempotency_key(
        order_ref=ORDER,
        vendor_ref=VENDOR,
        destination=destination,
        amount=Decimal(amount),
        currency=currency,
    )


# --- A. the key names everything that identifies the request ------------------------------------


def test_incident_2026_11_14_transfer_key_names_destination_amount_and_currency() -> None:
    assert key() == f"transfer:{ORDER}:{VENDOR}:acct_real_1:2782:EUR"
    assert key("acct_demo_1") != key("acct_real_1")  # a relinked vendor gets a fresh key
    assert key(amount="27.81") != key()
    assert key(currency="USD") != key()
    assert key() == key()  # stable: a plain retry replays, never pays twice


def test_incident_2026_11_14_transfer_key_stays_within_stripes_255_characters() -> None:
    long_key = key(destination="acct_" + "x" * 300)
    assert len(long_key) <= 255
    assert long_key.startswith(f"transfer:{ORDER}:{VENDOR}:")
    assert long_key != key(destination="acct_" + "y" * 300)


def _paid(client: FakeStripeClient) -> tuple[StripePaymentProvider, Any]:
    provider = StripePaymentProvider(client, webhook_secret=secrets.token_urlsafe(16))
    intent = client.create_payment_intent(
        amount_minor=5000,
        currency="EUR",
        transfer_group="order-9",
        methods=["card"],
        metadata={},
        idempotency_key="pi-key",
    )
    return provider, intent


def _ask(intent: Any, destination: str, *, reference: str = "vo-1") -> TransferRequest:
    return TransferRequest(
        "order-9",
        intent.id,
        intent.charge_id,
        destination,
        Decimal("27.82"),
        "EUR",
        key(destination),
        reference,
    )


def test_incident_2026_11_14_a_relinked_destination_is_paid_despite_a_cached_error() -> None:
    client = FakeStripeClient()
    provider, intent = _paid(client)
    client.refused_destinations.add("acct_old_1")
    with pytest.raises(ProviderError):
        provider.transfer(_ask(intent, "acct_old_1"))
    paid = provider.transfer(_ask(intent, "acct_new_1"))  # vendor linked to a real account
    assert client.transfers[paid.transfer_ref].destination == "acct_new_1"


def test_incident_2026_11_14_a_transfer_to_the_old_destination_is_never_doubled() -> None:
    client = FakeStripeClient()
    provider, intent = _paid(client)
    first = provider.transfer(_ask(intent, "acct_old_1"))
    with pytest.raises(TransferConflict) as conflict:
        provider.transfer(_ask(intent, "acct_new_1"))
    assert conflict.value.transfer_ref == first.transfer_ref
    assert list(client.transfers) == [first.transfer_ref]


def test_the_fake_provider_also_refuses_a_second_destination_for_one_vendor_order() -> None:
    provider = FakePaymentProvider()
    paid = provider.create_payment(
        PaymentRequest("order-1", "EUR", Decimal("50.00"), (), Decimal("50.00"), "pk-1")
    )
    old = TransferRequest(
        "order-1", paid.provider_ref, paid.charge_ref, "acct-a", Decimal("5.00"), "EUR", "t-a", "vo"
    )
    first = provider.transfer(old)
    with pytest.raises(TransferConflict):
        provider.transfer(
            TransferRequest(
                "order-1",
                paid.provider_ref,
                paid.charge_ref,
                "acct-b",
                Decimal("5.00"),
                "EUR",
                "t-b",
                "vo",
            )
        )
    assert provider.transfer(old) == first


class _ReversalSpy(FakeStripeClient):
    def __init__(self) -> None:
        super().__init__()
        self.reversal_keys: list[str] = []

    def create_transfer_reversal(
        self, *, transfer_id: str, amount_minor: int, idempotency_key: str
    ) -> str:
        self.reversal_keys.append(idempotency_key)
        return super().create_transfer_reversal(
            transfer_id=transfer_id, amount_minor=amount_minor, idempotency_key=idempotency_key
        )


def test_incident_2026_11_14_reversal_key_names_the_transfer_and_the_amount() -> None:
    client = _ReversalSpy()
    provider = StripePaymentProvider(client, webhook_secret=secrets.token_urlsafe(16))
    paid = provider.create_payment(
        PaymentRequest(
            "order-9",
            "EUR",
            Decimal("100.00"),
            (VendorShare("acct_a", Decimal("60.00")),),
            Decimal("40.00"),
            "k",
        )
    )
    provider.refund(RefundRequest(paid.provider_ref, Decimal("50.00"), "EUR", "rf-1"))
    (transfer_ref,) = paid.transfer_refs
    assert client.reversal_keys == [f"rf-1:reverse:{transfer_ref}"]


# --- B. a demo payout account never reaches Stripe ----------------------------------------------


def test_incident_2026_11_14_a_demo_account_never_reaches_stripe() -> None:
    client = FakeStripeClient()
    provider, intent = _paid(client)
    before = list(client.calls)
    with pytest.raises(ProviderError):
        provider.transfer(_ask(intent, f"{stripe_engine.DEMO_ACCOUNT_PREFIX}1"))
    assert client.calls == before
    assert client.transfers == {}


# --- C. Stripe's error type and code are kept, never the body or the key ------------------------


def _failing(status: int, body: bytes, key_value: str) -> HttpStripeClient:
    def transport(request: urllib.request.Request) -> bytes:
        raise urllib.error.HTTPError(request.full_url, status, "Bad", Message(), io.BytesIO(body))

    return HttpStripeClient(key_value, transport=transport)


def test_incident_2026_11_14_stripe_errors_keep_type_and_code_not_the_message() -> None:
    secret = secrets.token_urlsafe(16)
    idem = "transfer:order:vendor"
    body = json.dumps(
        {
            "error": {
                "type": "idempotency_error",
                "message": f"Keys for idempotent requests ... {idem} ... destination=acct_x",
            }
        }
    ).encode()
    with pytest.raises(StripeError) as caught:
        _failing(400, body, secret).create_transfer(
            amount_minor=100,
            currency="EUR",
            destination="acct_x",
            transfer_group="g",
            source_transaction="ch_1",
            metadata={},
            idempotency_key=idem,
        )
    error = caught.value
    assert (error.status, error.error_type, error.code, error.decline_code) == (
        400,
        "idempotency_error",
        "",
        "",
    )
    assert str(error) == "Stripe 400 idempotency_error on /transfers"
    assert secret not in str(error)
    assert idem not in str(error)
    assert "acct_x" not in str(error)


def test_a_card_decline_keeps_type_code_and_decline_code() -> None:
    body = json.dumps(
        {"error": {"type": "card_error", "code": "card_declined", "decline_code": "do_not_honor"}}
    ).encode()
    with pytest.raises(StripeError) as caught:
        _failing(402, body, secrets.token_urlsafe(16)).get_payment_intent("pi_1")
    expected = "Stripe 402 card_error card_declined do_not_honor on /payment_intents/pi_1"
    assert str(caught.value) == expected
    assert caught.value.decline_code == "do_not_honor"


@pytest.mark.parametrize(
    "body",
    [b"not json", b"[]", b'{"error": "x"}', json.dumps({"error": {"type": "bad type!"}}).encode()],
)
def test_an_unreadable_or_odd_error_body_still_gives_the_status(body: bytes) -> None:
    with pytest.raises(StripeError) as caught:
        _failing(500, body, secrets.token_urlsafe(16)).get_payment_intent("pi_1")
    assert str(caught.value) == "Stripe 500 on /payment_intents/pi_1"
    assert caught.value.status == 500
