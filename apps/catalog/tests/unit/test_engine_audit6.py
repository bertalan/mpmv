"""CAT.14 audit fixes: a variant override and a sale price are above 0.00; variant visibility."""

from dataclasses import dataclass
from decimal import Decimal

import pytest

from apps.catalog import engine


@dataclass
class Priced:
    sales_mode: str
    price_net: Decimal | None


@dataclass
class Overridden:
    price_net_override: Decimal | None


def test_a_zero_sale_price_is_refused() -> None:
    errors = engine.price_errors(Decimal("10.00"), Decimal("0.00"), None, None)
    assert errors == {"sale_price_net": engine.ZERO_SALE_PRICE}


def test_a_positive_sale_price_below_the_price_is_valid() -> None:
    assert engine.price_errors(Decimal("10.00"), Decimal("0.01"), None, None) == {}


def test_a_zero_override_is_refused_and_none_or_positive_is_fine() -> None:
    assert engine.override_error(Decimal("0.00")) == engine.ZERO_OVERRIDE
    assert engine.override_error(None) is None
    assert engine.override_error(Decimal("0.01")) is None


def test_a_variant_without_override_shows_the_product_price() -> None:
    product = Priced("buy", Decimal("20.00"))
    assert engine.public_variant_price(product, Overridden(None)) == Decimal("20.00")


def test_a_variant_override_is_its_public_price() -> None:
    product = Priced("buy", Decimal("20.00"))
    assert engine.public_variant_price(product, Overridden(Decimal("25.00"))) == Decimal("25.00")


@pytest.mark.parametrize("mode", ["on_request", "catalog_only"])
def test_price_on_request_hides_every_variant_price(mode: str) -> None:
    product = Priced(mode, Decimal("20.00"))
    assert engine.public_variant_price(product, Overridden(Decimal("25.00"))) is None
    assert engine.public_variant_price(product, Overridden(None)) is None
