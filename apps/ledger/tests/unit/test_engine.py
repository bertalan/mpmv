"""VEN.4: pure ledger rules (no database)."""

from decimal import Decimal

import pytest

from apps.ledger import engine


def test_amounts_are_rounded_half_up_to_cents() -> None:
    assert engine.money(Decimal("1.005")) == Decimal("1.01")
    assert engine.money(Decimal("-1.005")) == Decimal("-1.01")
    assert engine.money(Decimal("2")) == Decimal("2.00")


def test_floats_are_refused() -> None:
    with pytest.raises(TypeError):
        engine.money(1.5)  # type: ignore[arg-type]


@pytest.mark.parametrize("kind", sorted(engine.CREDIT_KINDS))
def test_credit_kinds_need_a_positive_amount(kind: str) -> None:
    assert engine.check_entry(kind, Decimal("1.00")) == Decimal("1.00")
    with pytest.raises(engine.InvalidEntry, match="credit"):
        engine.check_entry(kind, Decimal("-1.00"))


@pytest.mark.parametrize("kind", sorted(engine.DEBIT_KINDS))
def test_debit_kinds_need_a_negative_amount(kind: str) -> None:
    assert engine.check_entry(kind, Decimal("-1.00")) == Decimal("-1.00")
    with pytest.raises(engine.InvalidEntry, match="debit"):
        engine.check_entry(kind, Decimal("1.00"))


def test_adjustments_may_go_either_way_but_never_zero() -> None:
    assert engine.check_entry("adjustment", Decimal("3.00")) == Decimal("3.00")
    assert engine.check_entry("adjustment", Decimal("-3.00")) == Decimal("-3.00")
    with pytest.raises(engine.InvalidEntry, match="zero"):
        engine.check_entry("adjustment", Decimal("0.001"))


def test_an_unknown_kind_is_refused() -> None:
    with pytest.raises(engine.InvalidEntry, match="kind"):
        engine.check_entry("gift", Decimal("1.00"))


def test_every_kind_has_exactly_one_sign_rule() -> None:
    assert engine.CREDIT_KINDS.isdisjoint(engine.DEBIT_KINDS)
    assert engine.CREDIT_KINDS | engine.DEBIT_KINDS | {"adjustment"} == engine.KINDS


def test_only_euro_is_accepted() -> None:
    engine.check_currency("EUR")
    with pytest.raises(engine.InvalidEntry, match="currency"):
        engine.check_currency("USD")


def test_balance_is_the_sum_and_may_be_negative() -> None:
    assert engine.balance([]) == Decimal("0.00")
    assert engine.balance([Decimal("10.00"), Decimal("-2.50")]) == Decimal("7.50")
    assert engine.balance([Decimal("10.00"), Decimal("-12.50")]) == Decimal("-2.50")


def test_a_statement_adds_the_period_to_the_opening_balance() -> None:
    statement = engine.build_statement(
        Decimal("5.00"), {"sale": Decimal("100.00"), "commission": Decimal("-10.00")}
    )
    assert statement.opening == Decimal("5.00")
    assert statement.credits == Decimal("100.00")
    assert statement.debits == Decimal("-10.00")
    assert statement.closing == Decimal("95.00")
    assert statement.by_kind == {"sale": Decimal("100.00"), "commission": Decimal("-10.00")}
