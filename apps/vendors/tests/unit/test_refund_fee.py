"""VEN.33 / PAY.4: the pure refund fee (provider cost + surcharge, rounded up to the euro)."""

import ast
import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from apps.vendors import refund_fee as rf

D = Decimal


def rule(surcharge: str = "2.00", **kw: object) -> rf.RefundFeeRule:
    return rf.RefundFeeRule(surcharge=D(surcharge), **kw)


@pytest.mark.parametrize(
    ("cost", "expected"),
    [
        ("3.15", "6.00"),  # owner example: 5.15 -> 6
        ("1.37", "4.00"),
        ("1.00", "3.00"),  # an exact total stays
        ("1.20", "4.00"),  # second partial refund: 3.20 -> 4
        ("0.00", "2.00"),
    ],
)
def test_refund_fee_rounds_up_to_whole_euro(cost: str, expected: str) -> None:
    assert rf.refund_fee(D(cost), rule()) == D(expected)


def test_exact_three_stays_three() -> None:
    assert rf.refund_fee(D("1.00"), rule()) == D("3.00")
    assert rf.refund_fee(D("3.00"), rule("0")) == D("3.00")


def test_zero_surcharge_zero_cost_is_zero() -> None:
    assert rf.refund_fee(D("0.00"), rule("0")) == D("0.00")


def test_plan_override_changes_result() -> None:
    assert rf.refund_fee(D("3.15"), rule("2.00")) == D("6.00")
    assert rf.refund_fee(D("3.15"), rule("3.00")) == D("7.00")
    assert rf.refund_fee(D("3.15"), rule("0.00")) == D("4.00")


def test_every_refund_event_carries_the_surcharge() -> None:
    first = rf.refund_fee(D("3.15"), rule())
    second = rf.refund_fee(D("1.20"), rule())
    assert (first, second) == (D("6.00"), D("4.00"))


def test_chargeback_dispute_cost_is_the_provider_cost() -> None:
    assert rf.refund_fee(D("15.00"), rule()) == D("17.00")


def test_platform_error_charges_nothing() -> None:
    fee = rf.refund_fee(
        D("3.15"), rule(), platform_error=rf.PlatformErrorExclusion("gateway bug", "staff")
    )
    assert fee == D("0.00")


def test_exclusion_needs_a_reason() -> None:
    for reason in ("", "   "):
        with pytest.raises(rf.ReasonRequired):
            rf.PlatformErrorExclusion(reason, "staff")


def test_cause_is_not_an_input() -> None:
    # No split by cause: withdrawal, defect and vendor error all charge the same fee.
    assert rf.refund_fee(D("3.15"), rule()) == D("6.00")


def test_negative_cost_refused() -> None:
    with pytest.raises(ValueError, match="negative"):
        rf.refund_fee(D("-0.01"), rule())


def test_negative_surcharge_refused() -> None:
    with pytest.raises(ValueError, match="negative"):
        rule("-1")


def test_rule_snapshot_roundtrip_keeps_old_surcharge() -> None:
    snap = rule("2.00").snapshot()
    assert snap == {"refund_fee_surcharge": "2.00"}
    later = rule("3.00")
    assert rf.RefundFeeRule.from_snapshot(snap).surcharge == D("2.00")
    assert later.surcharge == D("3.00")
    assert rf.refund_fee(D("3.15"), rf.RefundFeeRule.from_snapshot(snap)) == D("6.00")


def test_snapshot_missing_key_refused() -> None:
    with pytest.raises(ValueError, match="snapshot"):
        rf.RefundFeeRule.from_snapshot({})


def test_notice_of_fifteen_days_is_enough() -> None:
    rf.check_notice(dt.date(2026, 12, 1), dt.date(2026, 12, 16))


def test_notice_shorter_than_fifteen_days_refused() -> None:
    with pytest.raises(rf.NoticeTooShort):
        rf.check_notice(dt.date(2026, 12, 1), dt.date(2026, 12, 15))


def test_version_in_force_covers_order_creation_date() -> None:
    old = rf.SurchargeVersion(D("2.00"), dt.date(2026, 1, 1))
    new = rf.SurchargeVersion(D("3.00"), dt.date(2026, 12, 16))
    versions = [new, old]
    assert rf.version_in_force(versions, dt.date(2026, 12, 15)) == old
    assert rf.version_in_force(versions, dt.date(2026, 12, 16)) == new


def test_no_version_in_force_before_first_effective_date() -> None:
    only = rf.SurchargeVersion(D("2.00"), dt.date(2026, 1, 1))
    with pytest.raises(LookupError):
        rf.version_in_force([only], dt.date(2025, 1, 1))


def test_module_has_no_orm_import() -> None:
    tree = ast.parse(Path(rf.__file__).read_text())
    mods = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)] + [
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    ]
    assert not [m for m in mods if m.startswith("django") or m.endswith(".models")]
