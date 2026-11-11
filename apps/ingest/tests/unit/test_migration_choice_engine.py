"""MIG.16: the pure rules of the vendor migration choices (no database)."""

import datetime as dt
from dataclasses import replace

import pytest

from apps.ingest import engine

UTC = dt.UTC
CUTOVER = dt.datetime(2027, 3, 1, 12, 0, tzinfo=UTC)
DEADLINE = dt.datetime(2027, 2, 15, 12, 0, tzinfo=UTC)


def test_the_defaults_are_the_owner_decision() -> None:
    assert (
        engine.ChoiceValues(
            period_months=24,
            data_types=("orders", "customers", "products", "reviews"),
            include_unpublished=False,
            login_window_months=12,
        )
        == engine.DEFAULT_CHOICE
    )
    assert engine.validate_choice(engine.DEFAULT_CHOICE) == {}


def test_the_login_window_offers_exactly_seven_values() -> None:
    assert engine.LOGIN_WINDOWS == (1, 2, 3, 6, 12, 24, 36)


@pytest.mark.parametrize("window", engine.LOGIN_WINDOWS)
def test_each_login_window_is_valid(window: int) -> None:
    assert engine.validate_choice(replace(engine.DEFAULT_CHOICE, login_window_months=window)) == {}


@pytest.mark.parametrize("window", [0, 4, 5, 7, 13, 18, 37, 48, -1])
def test_any_other_login_window_is_refused(window: int) -> None:
    errors = engine.validate_choice(replace(engine.DEFAULT_CHOICE, login_window_months=window))
    assert "login_window_months" in errors


@pytest.mark.parametrize("period", [12, 24, 36, None])
def test_the_periods_are_12_24_36_or_all(period: int | None) -> None:
    assert engine.validate_choice(replace(engine.DEFAULT_CHOICE, period_months=period)) == {}


@pytest.mark.parametrize("period", [0, 6, 18, 48, 120])
def test_any_other_period_is_refused(period: int) -> None:
    errors = engine.validate_choice(replace(engine.DEFAULT_CHOICE, period_months=period))
    assert "period_months" in errors


def test_an_unknown_data_type_is_refused() -> None:
    values = replace(engine.DEFAULT_CHOICE, data_types=("orders", "passwords"))
    assert "data_types" in engine.validate_choice(values)


def test_customers_need_their_orders() -> None:
    values = replace(engine.DEFAULT_CHOICE, data_types=("customers", "products"))
    assert "data_types" in engine.validate_choice(values)
    only_products = replace(engine.DEFAULT_CHOICE, data_types=("products",))
    assert engine.validate_choice(only_products) == {}


def test_data_types_are_unique_and_in_canonical_order() -> None:
    assert engine.normalise_data_types(["reviews", "orders", "orders"]) == ("orders", "reviews")
    assert engine.normalise_data_types(["zzz", "orders"]) == ("orders", "zzz")


def test_the_deadline_is_14_days_before_the_cutover() -> None:
    assert engine.choice_deadline(CUTOVER) == DEADLINE


@pytest.mark.parametrize(
    ("days_before", "expected"),
    [(30, None), (14.5, None), (14, 14), (8, 14), (7, 7), (2, 7), (1, 1), (0.1, 1)],
)
def test_the_due_reminder_is_the_closest_offset_reached(
    days_before: float, expected: int | None
) -> None:
    now = DEADLINE - dt.timedelta(days=days_before)
    assert engine.due_reminder(now, DEADLINE) == expected


def test_no_reminder_is_due_at_or_after_the_deadline() -> None:
    assert engine.due_reminder(DEADLINE, DEADLINE) is None
    assert engine.due_reminder(DEADLINE + dt.timedelta(days=1), DEADLINE) is None


def test_months_before_clamps_the_day() -> None:
    assert engine.months_before(dt.datetime(2027, 3, 31, tzinfo=UTC), 1) == dt.datetime(
        2027, 2, 28, tzinfo=UTC
    )
    assert engine.months_before(dt.datetime(2027, 1, 15, tzinfo=UTC), 13) == dt.datetime(
        2025, 12, 15, tzinfo=UTC
    )
