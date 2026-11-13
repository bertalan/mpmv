"""CMP.2: pure statement lines and carry-over (T-8 refund fee and refunds, T-1 / T-5 reductions)."""

from decimal import Decimal

import pytest

from apps.compliance import commission_statement as cs
from apps.vendors.refund_fee import PlatformErrorExclusion, RefundFeeRule

D = Decimal
RULE = RefundFeeRule(surcharge=D("2.00"))


def commission(order: str, amount: str) -> cs.CommissionEarned:
    return cs.CommissionEarned(order_ref=order, amount=D(amount))


def kinds(result: cs.Statement) -> list[str]:
    return [line.kind for line in result.lines]


def test_commissions_only() -> None:
    result = cs.build_statement(commissions=[commission("O1", "10.00"), commission("O2", "5.00")])
    assert result.total == D("15.00")
    assert result.carry_over_out == D("0.00")
    assert kinds(result) == [cs.COMMISSION, cs.COMMISSION]


def test_t8_refund_fee_is_a_line_of_its_own_with_the_commission_vat_treatment() -> None:
    refund = cs.RefundEvent(
        refund_ref="R1",
        order_ref="O1",
        commission_given_back=D("0.00"),
        provider_cost=D("3.15"),
        rule=RULE,
    )
    result = cs.build_statement(commissions=[commission("O1", "10.00")], refunds=[refund])
    fee = [line for line in result.lines if line.kind == cs.REFUND_FEE]
    assert len(fee) == 1
    assert fee[0].amount == D("6.00")  # 3.15 + 2.00 = 5.15 -> 6
    assert fee[0].vat_treatment == cs.COMMISSION_VAT
    assert result.total == D("16.00")


def test_t8_every_refund_event_has_its_own_fee_line() -> None:
    refunds = [
        cs.RefundEvent("R1", "O1", D("0.00"), D("3.15"), RULE),
        cs.RefundEvent("R2", "O1", D("0.00"), D("1.20"), RULE),
    ]
    result = cs.build_statement(commissions=[commission("O1", "10.00")], refunds=refunds)
    fees = [line.amount for line in result.lines if line.kind == cs.REFUND_FEE]
    assert fees == [D("6.00"), D("4.00")]


def test_t8_platform_error_refund_has_no_fee_line() -> None:
    refund = cs.RefundEvent(
        "R1",
        "O1",
        D("1.00"),
        D("3.15"),
        RULE,
        platform_error=PlatformErrorExclusion(reason="gateway fault", set_by="staff"),
    )
    result = cs.build_statement(commissions=[commission("O1", "10.00")], refunds=[refund])
    assert cs.REFUND_FEE not in kinds(result)
    assert result.total == D("9.00")


def test_t8_refund_before_invoicing_keeps_gross_line_plus_negative_line() -> None:
    refund = cs.RefundEvent("R1", "O1", D("4.00"), D("1.00"), RULE)
    result = cs.build_statement(commissions=[commission("O1", "10.00")], refunds=[refund])
    assert kinds(result)[:2] == [cs.COMMISSION, cs.COMMISSION_REFUNDED]
    assert result.lines[0].amount == D("10.00")
    assert result.lines[1].amount == D("-4.00")
    assert result.total == D("9.00")  # 10 - 4 + fee 3.00


def test_t8_refund_after_invoicing_as_negative_line_references_the_earlier_statement() -> None:
    refund = cs.RefundEvent(
        "R1",
        "O0",
        D("4.00"),
        D("0.00"),
        RULE,
        invoiced_statement_ref="ST-2026-09",
        after_invoicing=cs.AFTER_NEGATIVE_LINE,
    )
    result = cs.build_statement(commissions=[commission("O1", "10.00")], refunds=[refund])
    line = next(x for x in result.lines if x.kind == cs.COMMISSION_REFUNDED_PRIOR)
    assert line.amount == D("-4.00")
    assert line.references == "ST-2026-09"
    assert result.total == D("8.00")  # 10 - 4 + fee 2.00


def test_t8_refund_after_invoicing_as_credit_note_is_listed_but_not_in_the_total() -> None:
    refund = cs.RefundEvent(
        "R1",
        "O0",
        D("4.00"),
        D("0.00"),
        RULE,
        invoiced_statement_ref="ST-2026-09",
        after_invoicing=cs.AFTER_CREDIT_NOTE,
    )
    result = cs.build_statement(commissions=[commission("O1", "10.00")], refunds=[refund])
    note = next(x for x in result.lines if x.kind == cs.CREDIT_NOTE)
    assert note.amount == D("-4.00")
    assert note.references == "ST-2026-09"
    assert not note.in_total
    assert result.total == D("12.00")  # 10 + fee 2.00


def test_after_invoicing_needs_a_reference_and_a_mode() -> None:
    with pytest.raises(ValueError, match="earlier statement"):
        cs.RefundEvent("R1", "O0", D("1.00"), D("0.00"), RULE, after_invoicing=cs.AFTER_CREDIT_NOTE)
    with pytest.raises(ValueError, match="earlier statement"):
        cs.RefundEvent("R1", "O0", D("1.00"), D("0.00"), RULE, invoiced_statement_ref="X")


def test_t1_platform_coupon_reduces_the_order_commission() -> None:
    result = cs.build_statement(
        commissions=[commission("O1", "10.00")],
        reductions=[cs.PlatformReduction("O1", cs.COUPON, D("3.00"))],
    )
    assert result.total == D("7.00")
    assert cs.PLATFORM_COUPON in kinds(result)


def test_t1_excess_over_one_orders_commission_is_netted_against_the_others() -> None:
    result = cs.build_statement(
        commissions=[commission("O1", "2.00"), commission("O2", "10.00")],
        reductions=[cs.PlatformReduction("O1", cs.COUPON, D("5.00"))],
    )
    assert result.total == D("7.00")
    assert result.carry_over_out == D("0.00")


def test_t5_platform_paid_free_shipping_reduces_the_commission_like_a_coupon() -> None:
    result = cs.build_statement(
        commissions=[commission("O1", "10.00")],
        reductions=[cs.PlatformReduction("O1", cs.FREE_SHIPPING, D("4.90"))],
    )
    assert result.total == D("5.10")
    assert cs.PLATFORM_FREE_SHIPPING in kinds(result)


def test_t1_excess_larger_than_the_period_carries_over_and_the_period_is_zero() -> None:
    first = cs.build_statement(
        commissions=[commission("O1", "2.00"), commission("O2", "3.00")],
        reductions=[cs.PlatformReduction("O1", cs.COUPON, D("12.00"))],
    )
    assert first.total == D("0.00")
    assert first.carry_over_out == D("7.00")
    assert all(line.kind != "payout" for line in first.lines)
    second = cs.build_statement(
        commissions=[commission("O3", "10.00")], carry_over_in=first.carry_over_out
    )
    assert second.total == D("3.00")
    assert second.carry_over_out == D("0.00")
    applied = next(x for x in second.lines if x.kind == cs.CREDIT_APPLIED)
    assert applied.amount == D("-7.00")


def test_carry_over_larger_than_the_next_period_keeps_the_remainder() -> None:
    result = cs.build_statement(commissions=[commission("O3", "4.00")], carry_over_in=D("7.00"))
    assert result.total == D("0.00")
    assert result.carry_over_out == D("3.00")


def test_a_negative_period_never_pays_cash_and_never_goes_below_zero() -> None:
    result = cs.build_statement(
        commissions=[commission("O1", "1.00")],
        refunds=[
            cs.RefundEvent(
                "R1",
                "O1",
                D("1.00"),
                D("0.00"),
                RULE,
                platform_error=PlatformErrorExclusion("e", "s"),
            )
        ],
        reductions=[cs.PlatformReduction("O1", cs.COUPON, D("5.00"))],
        carry_over_in=D("1.00"),
    )
    assert result.total == D("0.00")
    assert result.carry_over_out == D("6.00")
    assert result.total >= 0


def test_t1_closing_credit_is_conserved() -> None:
    result = cs.build_statement(
        commissions=[commission("O1", "10.00")],
        reductions=[cs.PlatformReduction("O1", cs.COUPON, D("25.50"))],
        carry_over_in=D("2.00"),
    )
    assert result.total == D("0.00")
    assert result.carry_over_out == D("17.50")


def test_amounts_are_rounded_half_up_per_line() -> None:
    result = cs.build_statement(commissions=[commission("O1", "0.005"), commission("O2", "0.015")])
    assert [x.amount for x in result.lines] == [D("0.01"), D("0.02")]


def test_negative_inputs_are_refused() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        cs.PlatformReduction("O1", cs.COUPON, D("-1.00"))
    with pytest.raises(ValueError, match="cannot be negative"):
        cs.build_statement(commissions=[commission("O1", "-1.00")])
    with pytest.raises(ValueError, match="cannot be negative"):
        cs.build_statement(commissions=[], carry_over_in=D("-1.00"))
