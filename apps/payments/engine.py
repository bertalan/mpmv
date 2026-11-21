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


class PaymentMismatch(Exception):
    """The payment asked for does not belong to the order it was asked for."""


class PaymentInProgress(Exception):
    """The customer's payment is processing or already succeeded: no form must be shown again."""


class ProviderError(Exception):
    """The provider refused or failed a call; the message is safe to log."""


class TransferConflict(ProviderError):
    """A transfer of this payment for this vendor order already exists with another destination
    or amount. Retrying cannot fix it and must never pay twice: staff check `transfer_ref`."""

    def __init__(self, message: str, *, transfer_ref: str) -> None:
        super().__init__(message)
        self.transfer_ref = transfer_ref


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
    # Shown to the customer's browser only, to confirm the payment there (no card data on our
    # server); never stored or logged.
    client_secret: str = field(default="", repr=False)


@dataclass(frozen=True)
class TransferRequest:
    """One vendor's share of a settled payment (PAY.3, D-08), sent after the charge exists.

    `charge_ref` may be empty when the confirmation did not carry it: the provider then reads it
    from the payment. `idempotency_key` names the order, the vendor, the destination, the amount
    and the currency (`stripe_engine.transfer_idempotency_key`): a plain retry reuses it, a
    relinked account gets a fresh one. `reference` is the vendor order's public id, for the
    provider's metadata and reconciliation: a transfer already made for it to another account is a
    `TransferConflict`, never a second transfer.
    """

    order_ref: str  # the transfer group: the order number
    provider_ref: str  # the payment (PaymentIntent) the charge belongs to
    charge_ref: str
    vendor_account_ref: str
    amount: Decimal
    currency: str
    idempotency_key: str
    reference: str = ""


@dataclass(frozen=True)
class TransferResult:
    transfer_ref: str
    amount: Decimal


@dataclass(frozen=True)
class TransferReversal:
    """Take `amount` back from one transfer the platform made to a vendor (PAY.4, D-41)."""

    transfer_ref: str
    amount: Decimal


@dataclass(frozen=True)
class RefundRequest:
    provider_ref: str
    amount: Decimal
    currency: str
    idempotency_key: str
    # D-08: a refund takes the vendors' shares back from the vendors (`reverse_transfer`), pro
    # rata over every transfer of the payment. Ignored when `reversals` is given.
    reverse_vendor_shares: bool = True
    # PAY.4: exactly these transfers are reversed, by these amounts, and no other. Used when one
    # vendor refunds its own package: the other vendors' shares are never touched. An empty
    # tuple with `reverse_vendor_shares=False` reverses nothing (a duplicate payment, or a
    # transfer that was never created).
    reversals: Sequence[TransferReversal] | None = None


@dataclass(frozen=True)
class RefundResult:
    refund_ref: str
    amount: Decimal
    # The real provider cost of the refund, from the balance transaction (PAY.4, D-05).
    provider_cost: Decimal
    reversal_refs: Sequence[str] = ()
    # The provider's status of the refund: "succeeded", or "pending" while the card network
    # has not confirmed it (ORD.20: a refund not succeeded 4 days before its `due_by` alerts staff).
    status: str = "succeeded"


class TransferReversalFailed(ProviderError):
    """The provider refund SUCCEEDED but taking the vendor's share back failed (PAY.4): the buyer
    is already refunded, so the caller must record the refund and retry only the reversal."""

    def __init__(self, message: str, result: "RefundResult") -> None:
        super().__init__(message)
        self.result = result


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

    def transfer(self, request: TransferRequest) -> TransferResult:
        """Capability 1b (PAY.3): pay one vendor its share of a settled payment, tied to the
        charge (D-08 `source_transaction`). Idempotent on the key and never a second transfer
        for the same payment and vendor account; raises `ProviderError` on failure."""
        ...

    def refund(self, request: RefundRequest) -> RefundResult:
        """Capability 2: full or partial refund, reporting the provider cost for PAY.4."""
        ...

    def reverse_transfers(
        self, *, idempotency_key: str, currency: str, reversals: Sequence[TransferReversal]
    ) -> Sequence[str]:
        """Retry only the transfer reversals of a refund whose provider refund went through; the
        keys are `<idempotency_key>:reverse:<transfer>`, the same as in `refund`."""
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
    "transfer",
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


# PAY.4 (owner decision 2026-11-21): the nightly step for an open stray-capture alert.
STRAY_AUTO_CLOSE_DAYS = 14
STRAY_WAIT = "wait"
STRAY_REMIND = "remind"
STRAY_CLOSE = "close"


def stray_alert_step(
    *,
    flagged_at: datetime,
    now: datetime,
    amount: Decimal,
    refunded: Decimal | None,
    reminded_today: bool,
) -> str:
    """What the nightly job does with an open stray-capture alert. Before 14 days: nothing. From
    then on: close it when the provider confirms a refund of the whole payment amount (`refunded`
    is None when the provider could not be read, which confirms nothing), else remind support,
    at most once a business day."""
    if (now - flagged_at).days < STRAY_AUTO_CLOSE_DAYS:
        return STRAY_WAIT
    if refunded is not None and amount > 0 and refunded >= amount:
        return STRAY_CLOSE
    return STRAY_WAIT if reminded_today else STRAY_REMIND
