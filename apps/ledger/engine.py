"""Pure ledger rules (VEN.4, spec 04): kinds, sign per kind, balance and statement arithmetic.

No ORM, no clock. The ledger is an operational record of what each vendor is owed and paid, not
accounting (spec 04): amounts are signed from the vendor's side, positive for a credit to the
vendor and negative for a debit, always in EUR, rounded `ROUND_HALF_UP` to the cent.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
CURRENCY = "EUR"

SALE = "sale"
SHIPPING = "shipping"
COMMISSION = "commission"
PAYMENT_FEE = "payment_fee"
REFUND = "refund"
COMMISSION_REVERSAL = "commission_reversal"
REFUND_FEE = "refund_fee"
ADJUSTMENT = "adjustment"
PAYOUT = "payout"
TRANSFER = "transfer"
PLAN_FEE = "plan_fee"
BUNDLE_DISCOUNT_VENDOR_FUNDED = "bundle_discount_vendor_funded"
BUNDLE_DISCOUNT_PLATFORM_FUNDED = "bundle_discount_platform_funded"

# Money the vendor is owed: the sale and shipping it collected, the commission given back on a
# refund, and a discount the platform funds.
CREDIT_KINDS = frozenset({SALE, SHIPPING, COMMISSION_REVERSAL, BUNDLE_DISCOUNT_PLATFORM_FUNDED})
# Money the vendor owes or was paid: fees, refunds, discounts it funds, and what was sent to it.
DEBIT_KINDS = frozenset(
    {
        COMMISSION,
        PAYMENT_FEE,
        REFUND,
        REFUND_FEE,
        PAYOUT,
        TRANSFER,
        PLAN_FEE,
        BUNDLE_DISCOUNT_VENDOR_FUNDED,
    }
)
KINDS = CREDIT_KINDS | DEBIT_KINDS | {ADJUSTMENT}


class LedgerError(Exception):
    """Base class of the ledger refusals."""


class InvalidEntry(LedgerError):
    """The kind, amount, currency or references of an entry are not acceptable."""


class IdempotencyConflict(LedgerError):
    """The idempotency key is already used by an entry with different content."""


def money(value: Decimal) -> Decimal:
    """`value` rounded half up to the cent. Floats are refused."""
    if isinstance(value, float):
        raise TypeError("float amounts are not allowed; use Decimal")
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def check_entry(kind: str, amount: Decimal) -> Decimal:
    """The rounded amount, or `InvalidEntry`: known kind, sign right for the kind, never zero."""
    if kind not in KINDS:
        raise InvalidEntry(f"unknown ledger kind {kind!r}")
    rounded = money(amount)
    if rounded == 0:
        raise InvalidEntry("a ledger entry cannot be zero")
    if kind in CREDIT_KINDS and rounded < 0:
        raise InvalidEntry(f"{kind} is a credit to the vendor: the amount must be positive")
    if kind in DEBIT_KINDS and rounded > 0:
        raise InvalidEntry(f"{kind} is a debit from the vendor: the amount must be negative")
    return rounded


def check_currency(currency: str) -> None:
    if currency != CURRENCY:
        raise InvalidEntry(f"unsupported currency {currency!r}: the ledger is in {CURRENCY}")


def balance(amounts: Iterable[Decimal]) -> Decimal:
    """What the vendor is owed: the sum of its entries. Negative means the vendor owes the
    platform; it is never refused (spec 04: set off against transfers, or a SEPA debit)."""
    return money(sum(amounts, Decimal("0")))


@dataclass(frozen=True)
class Statement:
    opening: Decimal
    credits: Decimal
    debits: Decimal
    closing: Decimal
    by_kind: Mapping[str, Decimal]


def build_statement(opening: Decimal, by_kind: Mapping[str, Decimal]) -> Statement:
    """A period: the opening balance, the credits and debits (adjustments count by sign)."""
    credits = balance(v for v in by_kind.values() if v > 0)
    debits = balance(v for v in by_kind.values() if v < 0)
    return Statement(
        opening=money(opening),
        credits=credits,
        debits=debits,
        closing=money(opening + credits + debits),
        by_kind=dict(by_kind),
    )
