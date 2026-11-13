"""Pure commission computation (VEN.3, spec 04 section 4.4; no ORM, no I/O).

`compute_commission` works on the rule snapshot an order froze (`CommissionRule.snapshot()`),
never on a live row, so a later rule change cannot touch an existing order (case PERC-CONGELATA).
A missing rule is a blocking error, never a silent 0% (case REGOLA-ZERO). The payment fee is on
the gross paid (case FEE-NETTO). No VAT is applied to the commission or the payment fee: the
platform's tax position is `PlatformOperator` configuration, not a code path here. Every line is
`Decimal` rounded `ROUND_HALF_UP`; the refund fee is the one explicit ceil (`refund_fee`).
Refunds are per event in `refund_lines`, so `compute_commission` stays the paid-time figure.
"""

import dataclasses
from collections.abc import Mapping
from decimal import Decimal
from typing import Final

from apps.core.money import round_line
from apps.vendors import engine
from apps.vendors.refund_fee import PlatformErrorExclusion, RefundFeeRule, refund_fee

HUNDRED: Final = Decimal("100")
ZERO: Final = Decimal("0.00")
_FIELDS: Final = ("percent", "base", "fee_percent", "fee_fixed", "fee_base", "promo_label")


@dataclasses.dataclass(frozen=True)
class CommissionResult:
    """Paid-time figures of one order. Refunds are separate: see `refund_lines`.

    `vendor_net` is what flows 2 and 3 transfer. The amounts are computed on `goods_net` as the
    caller passes it (the pre-coupon list base, T-6; PRM.5 passes that amount). VAT on the
    commission is applied by the caller according to `PlatformOperator.commission_vat_mode`.
    """

    commission: Decimal
    payment_fee: Decimal
    vendor_net: Decimal


@dataclasses.dataclass(frozen=True)
class RefundEvent:
    """One refund or chargeback; the platform-error exclusion is set per event (L-10)."""

    refunded_gross: Decimal
    provider_cost: Decimal
    platform_error: PlatformErrorExclusion | None = None


@dataclasses.dataclass(frozen=True)
class RefundLines:
    """Ledger lines of one refund event, all debits or credits on the vendor side."""

    commission_reversal: Decimal
    payment_fee_reversal: Decimal
    refund_fee: Decimal
    refunded_gross: Decimal

    @property
    def vendor_clawback(self) -> Decimal:
        """Taken back from the vendor: refunded gross minus the reversals, plus the refund fee."""
        return round_line(
            self.refunded_gross
            - self.commission_reversal
            - self.payment_fee_reversal
            + self.refund_fee
        )


def _parse(snapshot: Mapping[str, str]) -> dict[str, str]:
    for field in _FIELDS:
        if field not in snapshot:
            raise ValueError(f"The commission rule snapshot has no {field}.")
    values = {field: str(snapshot[field]) for field in _FIELDS}
    errors = engine.commission_rule_errors(
        percent=Decimal(values["percent"]),
        base=values["base"],
        fee_percent=Decimal(values["fee_percent"]),
        fee_fixed=Decimal(values["fee_fixed"]),
        fee_base=values["fee_base"],
        promo_label=values["promo_label"],
    )
    if errors:
        raise ValueError("; ".join(errors.values()))
    return values


def compute_commission(
    rule_snapshot: Mapping[str, str] | None,
    *,
    goods_net: Decimal,
    gross_paid: Decimal,
    shipping_net: Decimal = ZERO,
) -> CommissionResult:
    """Commission, payment fee and vendor net of an order at paid time (no refunds).

    Raises `engine.NoCommissionRule` when `rule_snapshot` is None and `ValueError` for an
    invalid snapshot or a negative amount.
    """
    if rule_snapshot is None:
        raise engine.NoCommissionRule("No commission rule applies; configure one first.")
    if min(goods_net, shipping_net, gross_paid) < 0:
        raise ValueError("Amounts cannot be negative.")
    rule = _parse(rule_snapshot)
    base = goods_net
    if rule["base"] == engine.NET_GOODS_AND_SHIPPING:
        base = goods_net + shipping_net
    commission = round_line(base * Decimal(rule["percent"]) / HUNDRED)
    payment_fee = round_line(
        gross_paid * Decimal(rule["fee_percent"]) / HUNDRED + Decimal(rule["fee_fixed"])
    )
    vendor_net = round_line(gross_paid - commission - payment_fee)
    return CommissionResult(commission, payment_fee, vendor_net)


def refund_lines(
    rule_snapshot: Mapping[str, str] | None,
    event: RefundEvent,
    *,
    goods_net: Decimal,
    gross_paid: Decimal,
    refund_fee_rule: RefundFeeRule,
    shipping_net: Decimal = ZERO,
) -> RefundLines:
    """The ledger lines of ONE refund event (D-05, L-10); call it once per event.

    The commission and the collection cost are reversed pro rata on the refunded share of
    `gross_paid` (`ROUND_HALF_UP` per line). The refund fee is provider cost plus the plan's
    surcharge, rounded up to the whole euro, or 0 when the event carries a platform-error
    exclusion. The fee is charged to the vendor only; it is never passed on to the customer.
    """
    if event.refunded_gross < 0 or event.provider_cost < 0:
        raise ValueError("Amounts cannot be negative.")
    if event.refunded_gross > gross_paid:
        raise ValueError("A refund cannot exceed the gross paid.")
    paid = compute_commission(
        rule_snapshot, goods_net=goods_net, gross_paid=gross_paid, shipping_net=shipping_net
    )
    share = event.refunded_gross / gross_paid if gross_paid else ZERO
    return RefundLines(
        commission_reversal=round_line(paid.commission * share),
        payment_fee_reversal=round_line(paid.payment_fee * share),
        refund_fee=refund_fee(
            event.provider_cost, refund_fee_rule, platform_error=event.platform_error
        ),
        refunded_gross=round_line(event.refunded_gross),
    )
