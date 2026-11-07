"""Money rules (spec 01 conventions).

- Amounts are `Decimal`, never `float`.
- Stored amounts have 2 decimals; internal calculations keep 4.
- Rounding is ROUND_HALF_UP and happens **per line**, before lines are added up.
- The currency is explicit: EUR.
"""

from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CURRENCY = "EUR"
CENT = Decimal("0.01")
INTERNAL = Decimal("0.0001")


def to_decimal(value: object) -> Decimal:
    """Convert an exact value (Decimal, int, numeric string) to Decimal; refuse floats."""
    if isinstance(value, bool):
        raise TypeError("booleans are not amounts")
    if isinstance(value, float):
        raise TypeError("float amounts are not allowed; pass a Decimal or a string")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        try:
            return Decimal(value.strip())
        except InvalidOperation as exc:
            raise ValueError(f"{value!r} is not a decimal number") from exc
    raise TypeError(f"unsupported amount type: {type(value).__name__}")


def round_line(amount: Decimal) -> Decimal:
    """Round one line amount to cents, half away from zero."""
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def round_internal(amount: Decimal) -> Decimal:
    """Round an intermediate result to 4 decimals."""
    return amount.quantize(INTERNAL, rounding=ROUND_HALF_UP)


def sum_lines(lines: Iterable[Decimal]) -> Decimal:
    """Total of line amounts, each rounded to cents first."""
    return sum((round_line(line) for line in lines), start=Decimal("0.00"))
