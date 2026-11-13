"""In-memory payment provider for tests and local development (PAY.5).

The only double the project allows for a payment provider: tests mock the provider through the
`PaymentProvider` interface, never Stripe's SDK. It makes no network call and holds no secret;
the webhook "signature" is an HMAC over a key generated for the instance.
"""

import hashlib
import hmac
import json
import secrets
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from .engine import (
    FeeCollection,
    InvalidSignature,
    PaymentMismatch,
    PaymentRequest,
    PaymentResult,
    PaymentStatus,
    PayoutRecord,
    ProviderError,
    RefundRequest,
    RefundResult,
    WebhookEvent,
    validate_payment_request,
)


class FakePaymentProvider:
    code = "fake"
    fee_collection = FeeCollection.SPLIT_AT_SOURCE
    collects_on_operator_account = False
    refund_cost = Decimal("0.30")

    def __init__(self) -> None:
        self.webhook_key = secrets.token_bytes(32)
        self._payments: dict[str, tuple[PaymentRequest, PaymentResult]] = {}
        self._by_key: dict[str, str] = {}
        self._refunds: dict[str, RefundResult] = {}
        self._refunded: dict[str, Decimal] = {}
        self._payouts: list[PayoutRecord] = []
        self.reversed_transfers: list[str] = []

    def supported_methods(self) -> Sequence[str]:
        return ("card",)

    def create_payment(self, request: PaymentRequest) -> PaymentResult:
        if request.idempotency_key in self._by_key:
            return self._payments[self._by_key[request.idempotency_key]][1]
        problems = validate_payment_request(request)
        if problems:
            raise ProviderError("; ".join(problems))
        if request.method not in self.supported_methods():
            raise ProviderError(f"unsupported method {request.method}")
        ref = f"fake_pay_{len(self._payments) + 1}"
        charge = f"fake_ch_{len(self._payments) + 1}"
        transfers = tuple(f"{ref}_tr_{i}" for i, _ in enumerate(request.shares, start=1))
        result = PaymentResult(
            ref,
            PaymentStatus.SUCCEEDED,
            charge,
            transfers,
            client_secret=f"{ref}_secret_{secrets.token_urlsafe(12)}",
        )
        self._payments[ref] = (request, result)
        self._by_key[request.idempotency_key] = ref
        for transfer, share in zip(transfers, request.shares, strict=True):
            self._payouts.append(
                PayoutRecord(
                    transfer,
                    share.vendor_account_ref,
                    share.amount,
                    request.currency,
                    datetime.now().astimezone(),
                    ref,
                )
            )
        return result

    def payment_client_secret(self, provider_ref: str, order_ref: str) -> str:
        if (
            provider_ref not in self._payments
            or self._payments[provider_ref][0].order_ref != order_ref
        ):
            raise PaymentMismatch(provider_ref)
        return self._payments[provider_ref][1].client_secret

    def refund(self, request: RefundRequest) -> RefundResult:
        key = f"refund:{request.idempotency_key}"
        if key in self._refunds:
            return self._refunds[key]
        if request.provider_ref not in self._payments:
            raise ProviderError("unknown payment")
        paid, result = self._payments[request.provider_ref]
        already = self._refunded.get(request.provider_ref, Decimal("0"))
        if request.amount <= 0 or already + request.amount > paid.amount:
            raise ProviderError("refund exceeds the payment")
        self._refunded[request.provider_ref] = already + request.amount
        if request.reverse_vendor_shares:
            self.reversed_transfers.extend(result.transfer_refs)
        refund = RefundResult(f"fake_re_{len(self._refunds) + 1}", request.amount, self.refund_cost)
        self._refunds[key] = refund
        return refund

    def list_payouts(self, since: datetime) -> Sequence[PayoutRecord]:
        return [payout for payout in self._payouts if payout.paid_at >= since]

    def sign(self, body: bytes) -> str:
        return hmac.new(self.webhook_key, body, hashlib.sha256).hexdigest()

    def parse_webhook(self, body: bytes, signature: str) -> WebhookEvent:
        if not hmac.compare_digest(self.sign(body), signature):
            raise InvalidSignature("signature does not match")
        data = json.loads(body)
        return WebhookEvent(str(data["id"]), str(data["type"]), dict(data.get("data", {})))
