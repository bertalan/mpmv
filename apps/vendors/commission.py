"""Pure commission computation (VEN.3, spec 04 section 4.4; no ORM, no I/O).

`compute_commission` works on the rule snapshot an order froze (`CommissionRule.snapshot()`),
never on a live row, so a later rule change cannot touch an existing order (case PERC-CONGELATA).
A missing rule is a blocking error, never a silent 0% (case REGOLA-ZERO). The payment fee is on
the gross paid (case FEE-NETTO). No VAT is applied to the commission or the payment fee: the
platform's tax position is `PlatformOperator` configuration, not a code path here. Every line is
`Decimal` rounded `ROUND_HALF_UP`; the refund fee is the one explicit ceil (`refund_fee`).
"""

import dataclasses
from collections.abc import Iterable, Mapping
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
    """The fee lines of one order and what is left to the vendor."""

    commission: Decimal
    payment_fee: Decimal
    refund_fees: tuple[Decimal, ...]
    vendor_net: Decimal

    @property
    def refund_fee_total(self) -> Decimal:
        return round_line(sum(self.refund_fees, ZERO))


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
    refund_provider_costs: Iterable[Decimal] = (),
    refund_fee_rule: RefundFeeRule | None = None,
    platform_error: PlatformErrorExclusion | None = None,
) -> CommissionResult:
    """Commission, payment fee and refund-fee lines for an order.

    Raises `engine.NoCommissionRule` when `rule_snapshot` is None and `ValueError` for an
    invalid snapshot, a negative amount, or refund costs without a `refund_fee_rule`.
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
    costs = tuple(refund_provider_costs)
    if costs and refund_fee_rule is None:
        raise ValueError("A refund fee needs the plan's refund fee rule.")
    refund_fees: tuple[Decimal, ...] = ()
    if refund_fee_rule is not None:
        refund_fees = tuple(
            refund_fee(cost, refund_fee_rule, platform_error=platform_error) for cost in costs
        )
    vendor_net = round_line(gross_paid - commission - payment_fee - sum(refund_fees, ZERO))
    return CommissionResult(commission, payment_fee, refund_fees, vendor_net)
