"""CMP.1: pure DAC7 consideration and fees rules (T-6, adviser opinion 2026-11-11)."""

from decimal import Decimal

from apps.compliance import engine

D = Decimal


def amounts(**override: object) -> engine.Dac7Amounts:
    fields: dict[str, object] = {
        "order_total": D("100.00"),
        "vendor_coupon": D("0"),
        "platform_coupon": D("0"),
        "fees": D("15.00"),
    }
    fields.update(override)
    return engine.dac7_order_amounts(**fields)  # type: ignore[arg-type]


def test_a_vendor_coupon_reports_consideration_75_and_fees_15() -> None:
    result = amounts(vendor_coupon=D("10.00"))
    assert (result.consideration, result.fees) == (D("75.00"), D("15.00"))


def test_a_platform_coupon_reports_consideration_85_and_fees_5() -> None:
    result = amounts(platform_coupon=D("10.00"))
    assert (result.consideration, result.fees) == (D("85.00"), D("5.00"))


def test_an_order_without_coupon_is_the_total_less_the_fees() -> None:
    result = amounts()
    assert (result.consideration, result.fees) == (D("85.00"), D("15.00"))


def test_the_amounts_are_rounded_half_up_to_cents() -> None:
    result = amounts(fees=D("1.005"))
    assert result.fees == D("1.01")
    assert result.consideration == D("98.99")


def test_a_platform_coupon_above_the_fees_leaves_a_negative_fee_to_net() -> None:
    result = amounts(platform_coupon=D("20.00"))
    assert result.fees == D("-5.00")
    assert result.consideration == D("85.00")


def test_a_quarter_sums_orders_and_never_reports_negative_fees() -> None:
    quarter = engine.dac7_quarter(
        [amounts(platform_coupon=D("20.00")), amounts(fees=D("2.00"), order_total=D("50"))]
    )
    assert quarter.transactions == 2
    assert quarter.fees == D("0.00")
    assert quarter.consideration == D("130.00")


def test_the_quarter_fees_net_an_excess_against_other_orders() -> None:
    quarter = engine.dac7_quarter([amounts(platform_coupon=D("20.00")), amounts()])
    assert quarter.fees == D("10.00")
    assert quarter.consideration == D("170.00")


def test_an_empty_quarter_is_zero() -> None:
    quarter = engine.dac7_quarter([])
    assert (quarter.transactions, quarter.consideration, quarter.fees) == (0, D("0"), D("0"))


def test_under_30_sales_and_2000_or_less_is_not_reported() -> None:
    assert not engine.dac7_reportable(sales=29, consideration=D("2000.00"))
    assert engine.dac7_reportable(sales=30, consideration=D("10.00"))
    assert engine.dac7_reportable(sales=5, consideration=D("2000.01"))


def test_operator_owned_and_non_eu_sellers_are_not_reported() -> None:
    assert engine.dac7_reportable(sales=100, consideration=D("9000"), country="DE")
    assert not engine.dac7_reportable(sales=100, consideration=D("9000"), country="US")
    assert not engine.dac7_reportable(
        sales=100, consideration=D("9000"), country="DE", operator_owned=True
    )
