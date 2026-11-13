"""VEN.2: commission rule validation and rule selection, pure (no database)."""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

import pytest

from apps.vendors import engine


def utc(year: int, month: int = 1) -> dt.datetime:
    return dt.datetime(year, month, 1, tzinfo=dt.UTC)


def error(**overrides: object) -> dict[str, str]:
    values: dict[str, object] = {
        "percent": Decimal("20.00"),
        "base": engine.NET_GOODS,
        "fee_percent": Decimal("4.00"),
        "fee_fixed": Decimal("2.00"),
        "fee_base": engine.GROSS_PAID,
        "promo_label": "",
    }
    values.update(overrides)
    return engine.commission_rule_errors(**values)  # type: ignore[arg-type]


def test_a_normal_rule_has_no_error() -> None:
    assert error() == {}


def test_a_zero_percent_without_a_promo_label_is_refused_no_implicit_zero_rule() -> None:
    assert "percent" in error(percent=Decimal("0"))
    assert "percent" in error(percent=Decimal("0.00"), promo_label="   ")


def test_a_zero_percent_with_an_explicit_label_is_accepted() -> None:
    assert error(percent=Decimal("0"), promo_label="operator-owned") == {}


@pytest.mark.parametrize("percent", [Decimal("-1"), Decimal("100.01"), Decimal("1000")])
def test_the_percent_stays_between_zero_and_one_hundred(percent: Decimal) -> None:
    assert "percent" in error(percent=percent, promo_label="x")


def test_the_payment_fee_parts_cannot_be_negative_or_above_the_range() -> None:
    assert "fee_percent" in error(fee_percent=Decimal("-0.01"))
    assert "fee_percent" in error(fee_percent=Decimal("100.01"))
    assert "fee_fixed" in error(fee_fixed=Decimal("-0.01"))
    assert error(fee_percent=Decimal("0"), fee_fixed=Decimal("0")) == {}


def test_the_bases_are_the_declared_ones() -> None:
    assert "base" in error(base="gross")
    assert error(base=engine.NET_GOODS_AND_SHIPPING) == {}
    assert "fee_base" in error(fee_base="net_goods")  # case FEE-NETTO: always the gross paid


@dataclass(frozen=True)
class Row:
    name: str
    vendor_id: int | None
    plan_id: int | None
    valid_from: dt.datetime
    valid_to: dt.datetime | None = None


DEFAULT = Row("default", None, None, utc(2026))
PLAN = Row("plan", None, 7, utc(2026))
VENDOR = Row("vendor", 3, None, utc(2026))
ALL = [DEFAULT, PLAN, VENDOR]


def test_the_vendor_rule_beats_the_plan_rule_and_the_platform_default() -> None:
    assert engine.select_rule(ALL, vendor_id=3, plan_id=7, at=utc(2027)) is VENDOR


def test_the_plan_rule_beats_the_default() -> None:
    assert engine.select_rule(ALL, vendor_id=4, plan_id=7, at=utc(2027)) is PLAN


def test_the_platform_default_is_the_last_resort() -> None:
    assert engine.select_rule(ALL, vendor_id=4, plan_id=8, at=utc(2027)) is DEFAULT
    assert engine.select_rule(ALL, vendor_id=4, plan_id=None, at=utc(2027)) is DEFAULT


def test_an_expired_vendor_rule_falls_back_to_the_next_level() -> None:
    expired = Row("vendor", 3, None, utc(2026), utc(2027))
    assert (
        engine.select_rule([DEFAULT, expired], vendor_id=3, plan_id=None, at=utc(2027)) is DEFAULT
    )


def test_no_rule_is_a_blocking_error_never_a_zero_commission() -> None:
    with pytest.raises(engine.NoCommissionRule):
        engine.select_rule([], vendor_id=3, plan_id=7, at=utc(2027))
    with pytest.raises(engine.NoCommissionRule):
        engine.select_rule(
            [Row("later", None, None, utc(2030))], vendor_id=3, plan_id=7, at=utc(2027)
        )


def test_two_rules_covering_one_level_at_one_instant_are_refused() -> None:
    twin = Row("twin", None, None, utc(2026))
    with pytest.raises(engine.OverlappingCommissionRules):
        engine.select_rule([DEFAULT, twin], vendor_id=4, plan_id=None, at=utc(2027))
