"""The payment provider contract (PAY.5, owner decision D-09), pure types and validation.

A payment method is added by writing a `PaymentProvider` (see `docs/dev/payment-providers.md`).
This module holds the pure part: the data the provider exchanges with the platform, the
`PaymentProvider` Protocol and `capability_problems`, which the registry runs before it accepts
a provider. No ORM, no Django, no network here.

Money is `Decimal`, rounded `ROUND_HALF_UP` by the caller, never a float. A provider never rounds.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class FeeCollection(StrEnum):
    """How the platform fee (commission and fees) is taken, the first required capability."""

    SPLIT_AT_SOURCE = "split_at_source"  # the provider separates fee and vendor payout (PAY.1)
    DEFERRED_RECEIVABLE = "deferred_receivable"  # the fee is a ledger receivable (PAY.6)


class PaymentStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class InvalidSignature(Exception):
    """A webhook body whose signature does not verify; the caller answers 400 and stores nothing."""


class ProviderError(Exception):
    """The provider refused or failed a call; the message is safe to log."""


@dataclass(frozen=True)
class VendorShare:
    """What one vendor receives from a payment, at the provider's connected account."""

    vendor_account_ref: str
    amount: Decimal


@dataclass(frozen=True)
class PaymentRequest:
    """One customer payment. `amount` = vendor shares + `platform_fee`, checked by the engine."""

    order_ref: str
    currency: str
    amount: Decimal
    shares: Sequence[VendorShare]
    platform_fee: Decimal
    idempotency_key: str
    method: str = "card"


@dataclass(frozen=True)
class PaymentResult:
    provider_ref: str
    status: PaymentStatus
    # Provider id of the charge each vendor transfer is tied to (D-08 `source_transaction`).
    charge_ref: str = ""
    transfer_refs: Sequence[str] = ()


@dataclass(frozen=True)
class RefundRequest:
    provider_ref: str
    amount: Decimal
    currency: str
    idempotency_key: str
    # D-08: a refund takes the vendors' shares back from the vendors (`reverse_transfer`).
    reverse_vendor_shares: bool = True


@dataclass(frozen=True)
class RefundResult:
    refund_ref: str
    amount: Decimal
    # The real provider cost of the refund, from the balance transaction (PAY.4, D-05).
    provider_cost: Decimal


@dataclass(frozen=True)
class PayoutRecord:
    """One line the provider reports for reconciliation with the ledger (PAY.3)."""

    provider_ref: str
    vendor_account_ref: str
    amount: Decimal
    currency: str
    paid_at: datetime
    payment_ref: str = ""


@dataclass(frozen=True)
class WebhookEvent:
    """A verified webhook. `event_id` is stable across redeliveries (idempotency, PAY.2)."""

    event_id: str
    type: str
    payload: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class PaymentProvider(Protocol):
    """What a payment provider must offer. Four capabilities, all required."""

    code: str
    fee_collection: FeeCollection
    # D-08: must be False. Customer money is never collected on an operator bank account.
    collects_on_operator_account: bool

    def supported_methods(self) -> Sequence[str]:
        """The methods checkout may offer, e.g. `card`; checkout offers only these (PAY.1)."""
        ...

    def create_payment(self, request: PaymentRequest) -> PaymentResult:
        """Capability 1: collect, with fee and vendor shares separated per `fee_collection`."""
        ...

    def refund(self, request: RefundRequest) -> RefundResult:
        """Capability 2: full or partial refund, reporting the provider cost for PAY.4."""
        ...

    def list_payouts(self, since: datetime) -> Sequence[PayoutRecord]:
        """Capability 3: payout lines since a date, for reconciliation with the ledger (PAY.3)."""
        ...

    def parse_webhook(self, body: bytes, signature: str) -> WebhookEvent:
        """Capability 4: verify the signature, raising `InvalidSignature`; stable event id."""
        ...


REQUIRED_METHODS = (
    "supported_methods",
    "create_payment",
    "refund",
    "list_payouts",
    "parse_webhook",
)


def capability_problems(provider: object) -> list[str]:
    """Everything that keeps `provider` from being registered; empty means it is acceptable."""
    problems: list[str] = []
    code = getattr(provider, "code", None)
    if not isinstance(code, str) or not code.strip() or code != code.strip().lower():
        problems.append("`code` must be a non-empty lowercase string")
    for name in REQUIRED_METHODS:
        if not callable(getattr(provider, name, None)):
            problems.append(f"missing capability method `{name}`")
    if not isinstance(getattr(provider, "fee_collection", None), FeeCollection):
        problems.append("`fee_collection` must be a FeeCollection (split or deferred collection)")
    collects = getattr(provider, "collects_on_operator_account", None)
    if collects is not False:
        problems.append("`collects_on_operator_account` must be False (D-08)")
    return problems


def validate_payment_request(request: PaymentRequest) -> list[str]:
    """Amount consistency every provider relies on: shares plus fee equal the charge."""
    problems: list[str] = []
    if request.amount <= 0:
        problems.append("amount must be positive")
    if request.platform_fee < 0 or any(share.amount < 0 for share in request.shares):
        problems.append("fee and shares must not be negative")
    if sum((share.amount for share in request.shares), Decimal("0")) + request.platform_fee != (
        request.amount
    ):
        problems.append("shares plus platform fee must equal the amount")
    if not request.idempotency_key:
        problems.append("an idempotency key is required")
    return problems
