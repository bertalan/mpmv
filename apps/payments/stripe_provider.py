"""The Stripe implementation of `PaymentProvider` (PAY.1, owner decisions D-08 and D-09).

Stripe Connect with separate charges and transfers: one PaymentIntent for the whole order, tied
to its vendors' transfers by a `transfer_group`. Each vendor transfer carries `source_transaction`
(the order's charge), so no free platform balance of third-party money exists. What stays on the
platform's Stripe balance after the transfers is the platform fee: split at source, no deferred
collection and no operator bank account as payee.

The provider talks to Stripe through `StripeClient` only (`stripe_client.py`).
"""

import json
import time
from collections.abc import Callable, Sequence
from datetime import datetime

from django.conf import settings

from . import stripe_engine
from .engine import (
    FeeCollection,
    InvalidSignature,
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
from .stripe_client import (
    HttpStripeClient,
    StripeClient,
    StripeError,
    StripeIntent,
)

_STATUS = {
    "succeeded": PaymentStatus.SUCCEEDED,
    "canceled": PaymentStatus.FAILED,
}


class StripePaymentProvider:
    code = "stripe"
    fee_collection = FeeCollection.SPLIT_AT_SOURCE
    collects_on_operator_account = False  # D-08: every payment goes through Stripe Connect

    def __init__(
        self,
        client: StripeClient,
        *,
        webhook_secret: str,
        tolerance: int = stripe_engine.DEFAULT_TOLERANCE_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._client = client
        self._webhook_secret = webhook_secret
        self._tolerance = tolerance
        self._clock = clock

    def supported_methods(self) -> Sequence[str]:
        return ("card",)

    # -- Capability 1: payment, fee and vendor shares separated

    def create_payment(self, request: PaymentRequest) -> PaymentResult:
        problems = validate_payment_request(request)
        if problems:
            raise ProviderError("; ".join(problems))
        if request.method not in self.supported_methods():
            raise ProviderError(f"unsupported method {request.method}")
        amount_minor = stripe_engine.to_minor(request.amount, request.currency)
        share_minor = [stripe_engine.to_minor(s.amount, request.currency) for s in request.shares]
        try:
            intent = self._client.create_payment_intent(
                amount_minor=amount_minor,
                currency=request.currency,
                transfer_group=request.order_ref,
                methods=[request.method],
                metadata={"order_ref": request.order_ref},
                idempotency_key=request.idempotency_key,
            )
            transfers: list[str] = []
            if intent.charge_id and intent.status == "succeeded":
                transfers = self._transfer_shares(request, intent, share_minor)
        except StripeError as error:
            raise ProviderError(str(error)) from error
        return PaymentResult(
            provider_ref=intent.id,
            status=_STATUS.get(intent.status, PaymentStatus.PENDING),
            charge_ref=intent.charge_id,
            transfer_refs=tuple(transfers),
        )

    def _transfer_shares(
        self, request: PaymentRequest, intent: StripeIntent, share_minor: Sequence[int]
    ) -> list[str]:
        refs: list[str] = []
        for index, (share, minor) in enumerate(zip(request.shares, share_minor, strict=True)):
            transfer = self._client.create_transfer(
                amount_minor=minor,
                currency=request.currency,
                destination=share.vendor_account_ref,
                transfer_group=request.order_ref,
                source_transaction=intent.charge_id,  # D-08: tied to the order's charge
                metadata={"payment_ref": intent.id, "order_ref": request.order_ref},
                idempotency_key=f"{request.idempotency_key}:transfer:{index}",
            )
            refs.append(transfer.id)
        return refs

    # -- Capability 2: refunds with the provider cost

    def refund(self, request: RefundRequest) -> RefundResult:
        if request.amount <= 0:
            raise ProviderError("refund amount must be positive")
        refund_minor = stripe_engine.to_minor(request.amount, request.currency)
        try:
            intent = self._client.get_payment_intent(request.provider_ref)
            refund = self._client.create_refund(
                payment_intent=request.provider_ref,
                amount_minor=refund_minor,
                idempotency_key=request.idempotency_key,
            )
            if request.reverse_vendor_shares:
                self._reverse_transfers(request, intent, refund_minor)
        except StripeError as error:
            raise ProviderError(str(error)) from error
        return RefundResult(
            refund_ref=refund.id,
            amount=stripe_engine.from_minor(refund.amount_minor, request.currency),
            provider_cost=stripe_engine.from_minor(refund.fee_minor, request.currency),
        )

    def _reverse_transfers(
        self, request: RefundRequest, intent: StripeIntent, refund_minor: int
    ) -> None:
        group = [
            t
            for t in self._client.list_transfers(transfer_group=intent.transfer_group)
            if t.payment_ref == intent.id
        ]
        amounts = stripe_engine.reversal_amounts(
            [t.amount_minor for t in group],
            refund_minor=refund_minor,
            charge_minor=intent.amount_minor,
        )
        for transfer, minor in zip(group, amounts, strict=True):
            if minor > 0:
                self._client.create_transfer_reversal(
                    transfer_id=transfer.id,
                    amount_minor=minor,
                    idempotency_key=f"{request.idempotency_key}:reverse:{transfer.id}",
                )

    # -- Capability 3: payout reconciliation

    def list_payouts(self, since: datetime) -> Sequence[PayoutRecord]:
        try:
            transfers = self._client.list_transfers(created_gte=since)
        except StripeError as error:
            raise ProviderError(str(error)) from error
        return [
            PayoutRecord(
                provider_ref=t.id,
                vendor_account_ref=t.destination,
                amount=stripe_engine.from_minor(t.amount_minor, t.currency),
                currency=t.currency,
                paid_at=t.created,
                payment_ref=t.payment_ref,
            )
            for t in transfers
            if t.created >= since
        ]

    # -- Capability 4: signed, idempotent webhooks

    def parse_webhook(self, body: bytes, signature: str) -> WebhookEvent:
        stripe_engine.verify_signature(
            body, signature, self._webhook_secret, now=self._clock(), tolerance=self._tolerance
        )
        try:
            data = json.loads(body)
            return WebhookEvent(str(data["id"]), str(data["type"]), dict(data.get("data", {})))
        except (ValueError, KeyError, TypeError):
            raise InvalidSignature("the signed body is not a Stripe event") from None


def client_from_settings() -> StripeClient:
    """The HTTP client the Manager uses for onboarding (tests pass a fake instead)."""
    return HttpStripeClient(settings.STRIPE_SECRET_KEY, api_version=settings.STRIPE_API_VERSION)


def provider_from_settings() -> StripePaymentProvider:
    """The registered factory: keys come from the environment, empty until configured."""
    return StripePaymentProvider(
        HttpStripeClient(settings.STRIPE_SECRET_KEY, api_version=settings.STRIPE_API_VERSION),
        webhook_secret=settings.STRIPE_WEBHOOK_SECRET,
        tolerance=settings.STRIPE_WEBHOOK_TOLERANCE_SECONDS,
    )


__all__ = ["StripePaymentProvider", "client_from_settings", "provider_from_settings"]
