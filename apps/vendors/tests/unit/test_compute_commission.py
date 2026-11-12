"""VEN.3: `compute_commission` pure function and the spec 04 §4.4 real cases (no database)."""

import ast
import datetime as dt
import pathlib
from decimal import Decimal

import pytest

from apps.vendors import commission, engine
from apps.vendors.refund_fee import PlatformErrorExclusion, RefundFeeRule

D = Decimal


def snap(**overrides: str) -> dict[str, str]:
    values = {
        "rule_id": "00000000-0000-0000-0000-000000000001",
        "version": "1",
        "percent": "20.00",
        "base": engine.NET_GOODS,
        "fee_percent": "4.00",
        "fee_fixed": "2.00",
        "fee_base": engine.GROSS_PAID,
        "promo_label": "",
        "valid_from": "2026-01-01T00:00:00+00:00",
    }
    values.update(overrides)
    return values


def test_case_1_example_twenty_percent_on_100_net_122_gross() -> None:
    result = commission.compute_commission(snap(), goods_net=D("100.00"), gross_paid=D("122.00"))
    assert result.commission == D("20.00")
    assert result.payment_fee == D("6.88")
    assert result.vendor_net == D("95.12")


def test_case_2_fee_netto_payment_fee_is_on_the_gross_not_the_net() -> None:
    # 183.56 * 4% + 2 = 9.3424 -> 9.34 (on the net 150.00 it would be 8.00)
    result = commission.compute_commission(snap(), goods_net=D("150.00"), gross_paid=D("183.56"))
    assert result.payment_fee == D("9.34")


def test_case_3_no_vat_on_commission_or_payment_fee() -> None:
    result = commission.compute_commission(snap(), goods_net=D("100.00"), gross_paid=D("122.00"))
    assert not any("vat" in field or "tax" in field for field in vars(result))
    assert result.commission + result.payment_fee + result.vendor_net == D("122.00")


def test_case_4_regola_zero_missing_rule_is_a_blocking_error() -> None:
    with pytest.raises(engine.NoCommissionRule):
        commission.compute_commission(None, goods_net=D("100.00"), gross_paid=D("122.00"))


def test_case_4_a_zero_percent_without_a_label_is_refused() -> None:
    with pytest.raises(ValueError, match="0%"):
        commission.compute_commission(
            snap(percent="0.00"), goods_net=D("100.00"), gross_paid=D("122.00")
        )


def test_a_labelled_zero_percent_promo_gives_zero_commission() -> None:
    result = commission.compute_commission(
        snap(percent="0.00", promo_label="launch promo"),
        goods_net=D("100.00"),
        gross_paid=D("122.00"),
    )
    assert result.commission == D("0.00")


def test_case_5_perc_congelata_the_snapshot_percent_wins_over_a_later_rule() -> None:
    frozen = snap(percent="15.00")
    # a later rule (20%) exists, but the order carries its own snapshot
    result = commission.compute_commission(frozen, goods_net=D("100.00"), gross_paid=D("122.00"))
    assert result.commission == D("15.00")


def test_case_5_perc_congelata_the_rule_valid_on_the_order_date_is_selected() -> None:
    from dataclasses import dataclass

    @dataclass
    class Row:
        percent: str
        valid_from: dt.datetime
        valid_to: dt.datetime | None
        vendor_id: int | None = None
        plan_id: int | None = None

    jan, jun = dt.datetime(2026, 1, 1, tzinfo=dt.UTC), dt.datetime(2026, 6, 1, tzinfo=dt.UTC)
    old = Row("15.00", jan, jun)
    new = Row("20.00", jun, None)
    order_date = dt.datetime(2026, 3, 1, tzinfo=dt.UTC)
    chosen = engine.select_rule([old, new], vendor_id=None, plan_id=None, at=order_date)
    assert chosen.percent == "15.00"


def test_commission_base_net_goods_and_shipping() -> None:
    result = commission.compute_commission(
        snap(base=engine.NET_GOODS_AND_SHIPPING),
        goods_net=D("100.00"),
        shipping_net=D("10.00"),
        gross_paid=D("134.20"),
    )
    assert result.commission == D("22.00")


def test_commission_base_net_goods_ignores_shipping() -> None:
    result = commission.compute_commission(
        snap(), goods_net=D("100.00"), shipping_net=D("10.00"), gross_paid=D("134.20")
    )
    assert result.commission == D("20.00")


def test_round_half_up_per_line() -> None:
    # 33.33 * 15% = 4.9995 -> 5.00; 10.00 * 4% + 0.125 = 0.525 -> 0.53 (half up, not half even)
    result = commission.compute_commission(
        snap(percent="15.00", fee_percent="4.00", fee_fixed="0.125"),
        goods_net=D("33.33"),
        gross_paid=D("10.00"),
    )
    assert result.commission == D("5.00")
    assert result.payment_fee == D("0.53")


def test_amounts_are_decimal_with_two_places_never_float() -> None:
    result = commission.compute_commission(snap(), goods_net=D("100"), gross_paid=D("122"))
    for value in (result.commission, result.payment_fee, result.vendor_net):
        assert isinstance(value, Decimal)
        assert value == value.quantize(D("0.01"))


def test_refund_fee_lines_are_included_and_rounded_up_to_the_euro() -> None:
    result = commission.compute_commission(
        snap(),
        goods_net=D("100.00"),
        gross_paid=D("122.00"),
        refund_provider_costs=[D("3.15"), D("1.20")],
        refund_fee_rule=RefundFeeRule(),
    )
    assert result.refund_fees == (D("6.00"), D("4.00"))
    assert result.refund_fee_total == D("10.00")
    assert result.vendor_net == D("85.12")


def test_refund_fee_is_zero_on_a_platform_error() -> None:
    result = commission.compute_commission(
        snap(),
        goods_net=D("100.00"),
        gross_paid=D("122.00"),
        refund_provider_costs=[D("3.15")],
        refund_fee_rule=RefundFeeRule(),
        platform_error=PlatformErrorExclusion(reason="gateway fault", set_by="staff"),
    )
    assert result.refund_fee_total == D("0.00")


def test_refund_costs_without_a_refund_fee_rule_are_refused() -> None:
    with pytest.raises(ValueError, match="refund"):
        commission.compute_commission(
            snap(), goods_net=D("100.00"), gross_paid=D("122.00"), refund_provider_costs=[D("1")]
        )


def test_a_snapshot_missing_a_field_is_refused() -> None:
    broken = snap()
    del broken["fee_fixed"]
    with pytest.raises(ValueError, match="fee_fixed"):
        commission.compute_commission(broken, goods_net=D("100.00"), gross_paid=D("122.00"))


def test_the_module_has_no_orm_or_io_import() -> None:
    tree = ast.parse(pathlib.Path(commission.__file__).read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not [m for m in imported if m.split(".")[0] in {"django", "requests"}]
    assert "apps.vendors.models" not in imported
