"""Pure catalog rules (spec 03 §3.2): SKU normalization and price validation. No ORM here."""

import datetime as dt
from decimal import Decimal

from apps.core.money import CENT, to_decimal

SKU_MAX_LENGTH = 64


class InvalidSku(ValueError):
    """The SKU is empty once normalized."""


def canonical_sku(value: str) -> str:
    """The stored form of a SKU: no surrounding whitespace, upper case. Never raises."""
    return value.strip().upper()


def normalize_sku(value: str) -> str:
    """`canonical_sku`, refusing a SKU that is empty once normalized."""
    sku = canonical_sku(value)
    if not sku:
        raise InvalidSku("A SKU cannot be empty.")
    return sku


def to_price(value: object) -> Decimal:
    """An exact, non-negative amount with 2 decimals.

    Floats are refused (TypeError). More than 2 decimals are refused, not rounded: a price is
    entered, not computed, so a third decimal is an input error.
    """
    amount = to_decimal(value)
    if not amount.is_finite():
        raise ValueError(f"{value!r} is not a finite amount")
    if amount != amount.quantize(CENT):
        raise ValueError(f"{value!r} has more than 2 decimals")
    if amount < 0:
        raise ValueError(f"{value!r} is negative")
    return amount.quantize(CENT)


def price_errors(
    price_net: Decimal,
    sale_price_net: Decimal | None,
    sale_from: dt.datetime | None,
    sale_to: dt.datetime | None,
) -> dict[str, str]:
    """Consistency of a regular price with its optional sale, by field name; empty when valid.

    Same rules as the database check constraints on Product.
    """
    errors: dict[str, str] = {}
    if sale_price_net is None:
        if sale_from is not None or sale_to is not None:
            errors["sale_price_net"] = "A sale period needs a sale price."
    elif sale_price_net >= price_net:
        errors["sale_price_net"] = "The sale price must be lower than the price."
    if sale_from is not None and sale_to is not None and sale_to <= sale_from:
        errors["sale_to"] = "The sale must end after it starts."
    return errors
