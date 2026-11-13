"""PAY.1: an order total is rounded half up to the currency's minor unit before it is sent."""

from decimal import Decimal

import pytest

from apps.payments import stripe_engine


@pytest.mark.parametrize(
    ("amount", "currency", "rounded", "minor"),
    [
        ("10.00", "EUR", "10.00", 1000),
        ("10.005", "EUR", "10.01", 1001),  # half up, not banker's
        ("10.004", "EUR", "10.00", 1000),
        ("0.01", "EUR", "0.01", 1),
        ("1234567.89", "EUR", "1234567.89", 123456789),
        ("10.50", "JPY", "11", 11),  # zero-decimal currency
        ("10.49", "JPY", "10", 10),
    ],
)
def test_rounding_and_minor_units(amount: str, currency: str, rounded: str, minor: int) -> None:
    result = stripe_engine.round_to_currency(Decimal(amount), currency)
    assert result == Decimal(rounded)
    assert stripe_engine.to_minor(result, currency) == minor
