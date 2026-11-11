"""Pure commission statement lines and carry-over (CMP.2; no ORM, no I/O).

Turns a period's events into statement lines and a closing carry-over credit. Rules (adviser
opinion 2026-11-11, owner decision T-1):

- T-8: the refund fee is a platform service with the commission's VAT treatment, a line of its own
  (one per refund event; none on a platform-error refund). Commission given back before the period
  is invoiced shows as the gross commission line plus a negative "commission on refunded orders"
  line. After invoicing: a credit note (listed, outside the total) or a negative line on the next
  statement, both referencing the earlier statement.
- T-1 and T-5: a platform coupon or platform-paid free shipping reduces the commission by the same
  amount; an excess over one order's commission is netted against the period's other commissions
  because every reduction is a line of the same total.
- The total of a period is never below zero. A negative subtotal leaves the period at zero and is
  carried over to the next period as a vendor credit against future commissions. A credit is
  never paid out in cash: no payout line exists.

Amounts are `Decimal`, rounded `ROUND_HALF_UP` per line; the refund fee comes from
`apps.vendors.refund_fee` (the ceil to the euro is its one exception). The ledger wiring that
feeds these inputs is a follow-up (apps/ledger is empty).
"""

import dataclasses
from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

from apps.vendors.refund_fee import PlatformErrorExclusion, RefundFeeRule, refund_fee

CENT: Final = Decimal("0.01")
ZERO: Final = Decimal("0.00")

# Line kinds.
COMMISSION: Final = "commission"
COMMISSION_REFUNDED: Final = "commission_refunded"
COMMISSION_REFUNDED_PRIOR: Final = "commission_refunded_prior_statement"
CREDIT_NOTE: Final = "credit_note"
REFUND_FEE: Final = "refund_fee"
PLATFORM_COUPON: Final = "platform_coupon"
PLATFORM_FREE_SHIPPING: Final = "platform_free_shipping"
CREDIT_APPLIED: Final = "credit_applied"

# Reduction kinds (input) and after-invoicing modes.
COUPON: Final = "coupon"
FREE_SHIPPING: Final = "free_shipping"
AFTER_CREDIT_NOTE: Final = "credit_note"
AFTER_NEGATIVE_LINE: Final = "negative_line"

COMMISSION_VAT: Final = "commission"  # the commission's VAT treatment, as the operator sets it


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _non_negative(value: Decimal, what: str) -> Decimal:
    if value < 0:
        raise ValueError(f"{what} cannot be negative.")
    return money(value)


@dataclasses.dataclass(frozen=True)
class CommissionEarned:
    order_ref: str
    amount: Decimal


@dataclasses.dataclass(frozen=True)
class PlatformReduction:
    """A platform coupon or platform-paid free shipping (Q-90 model, T-1, T-5)."""

    order_ref: str
    kind: str
    amount: Decimal

    def __post_init__(self) -> None:
        if self.kind not in (COUPON, FREE_SHIPPING):
            raise ValueError(f"Unknown reduction kind {self.kind!r}.")
        _non_negative(self.amount, "A reduction")


@dataclasses.dataclass(frozen=True)
class RefundEvent:
    refund_ref: str
    order_ref: str
    commission_given_back: Decimal
    provider_cost: Decimal
    rule: RefundFeeRule
    platform_error: PlatformErrorExclusion | None = None
    invoiced_statement_ref: str | None = None
    after_invoicing: str | None = None

    def __post_init__(self) -> None:
        _non_negative(self.commission_given_back, "Commission given back")
        if (self.invoiced_statement_ref is None) != (self.after_invoicing is None):
            raise ValueError("A refund after invoicing needs the earlier statement and a mode.")
        if self.after_invoicing not in (None, AFTER_CREDIT_NOTE, AFTER_NEGATIVE_LINE):
            raise ValueError(f"Unknown after-invoicing mode {self.after_invoicing!r}.")


@dataclasses.dataclass(frozen=True)
class StatementLine:
    kind: str
    amount: Decimal
    order_ref: str | None = None
    refund_ref: str | None = None
    references: str | None = None  # an earlier statement
    vat_treatment: str | None = None
    in_total: bool = True


@dataclasses.dataclass(frozen=True)
class Statement:
    lines: tuple[StatementLine, ...]
    subtotal: Decimal
    total: Decimal
    carry_over_in: Decimal
    carry_over_out: Decimal


def build_statement(
    *,
    commissions: Iterable[CommissionEarned],
    refunds: Iterable[RefundEvent] = (),
    reductions: Iterable[PlatformReduction] = (),
    carry_over_in: Decimal = ZERO,
) -> Statement:
    """The lines, the period total (never below zero) and the closing vendor credit."""
    opening = _non_negative(carry_over_in, "The carried-over credit")
    lines: list[StatementLine] = []
    for earned in commissions:
        lines.append(
            StatementLine(
                COMMISSION,
                _non_negative(earned.amount, "A commission"),
                order_ref=earned.order_ref,
                vat_treatment=COMMISSION_VAT,
            )
        )
    for refund in refunds:
        lines.extend(_refund_lines(refund))
    for reduction in reductions:
        kind = PLATFORM_COUPON if reduction.kind == COUPON else PLATFORM_FREE_SHIPPING
        lines.append(
            StatementLine(
                kind,
                -money(reduction.amount),
                order_ref=reduction.order_ref,
                vat_treatment=COMMISSION_VAT,
            )
        )
    subtotal = sum((x.amount for x in lines if x.in_total), ZERO)
    if subtotal < 0:
        # Never a negative period and never cash: the excess becomes a credit.
        return Statement(tuple(lines), subtotal, ZERO, opening, money(opening - subtotal))
    applied = min(opening, subtotal)
    if applied > 0:
        lines.append(StatementLine(CREDIT_APPLIED, -applied))
    return Statement(tuple(lines), subtotal, money(subtotal - applied), opening, opening - applied)


def _refund_lines(refund: RefundEvent) -> list[StatementLine]:
    lines: list[StatementLine] = []
    given_back = money(refund.commission_given_back)
    if given_back > 0:
        if refund.after_invoicing == AFTER_CREDIT_NOTE:
            lines.append(
                StatementLine(
                    CREDIT_NOTE,
                    -given_back,
                    order_ref=refund.order_ref,
                    refund_ref=refund.refund_ref,
                    references=refund.invoiced_statement_ref,
                    vat_treatment=COMMISSION_VAT,
                    in_total=False,
                )
            )
        elif refund.after_invoicing == AFTER_NEGATIVE_LINE:
            lines.append(
                StatementLine(
                    COMMISSION_REFUNDED_PRIOR,
                    -given_back,
                    order_ref=refund.order_ref,
                    refund_ref=refund.refund_ref,
                    references=refund.invoiced_statement_ref,
                    vat_treatment=COMMISSION_VAT,
                )
            )
        else:
            lines.append(
                StatementLine(
                    COMMISSION_REFUNDED,
                    -given_back,
                    order_ref=refund.order_ref,
                    refund_ref=refund.refund_ref,
                    vat_treatment=COMMISSION_VAT,
                )
            )
    fee = refund_fee(refund.provider_cost, refund.rule, platform_error=refund.platform_error)
    if fee > 0:
        lines.append(
            StatementLine(
                REFUND_FEE,
                fee,
                order_ref=refund.order_ref,
                refund_ref=refund.refund_ref,
                vat_treatment=COMMISSION_VAT,
            )
        )
    return lines
