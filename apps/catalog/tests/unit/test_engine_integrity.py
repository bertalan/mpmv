"""CAT.14: pure price rules of owner decision Q-31 (spec 03 §3.2 Rules, PRICE-ON-REQUEST)."""

from dataclasses import dataclass
from decimal import Decimal

import pytest

from apps.catalog import engine


@dataclass
class Priced:
    sales_mode: str
    price_net: Decimal | None


@pytest.mark.parametrize("mode", ["on_request", "catalog_only"])
def test_a_product_not_for_sale_has_no_public_price(mode: str) -> None:
    assert engine.public_price(Priced(mode, Decimal("120.00"))) is None


def test_a_product_for_sale_shows_its_price() -> None:
    assert engine.public_price(Priced("buy", Decimal("120.00"))) == Decimal("120.00")


def test_a_product_for_sale_without_a_price_has_no_public_price() -> None:
    assert engine.public_price(Priced("buy", None)) is None


def test_a_product_for_sale_needs_a_price() -> None:
    assert engine.price_required_error("buy", None) is not None
    assert engine.price_required_error("buy", Decimal("1.00")) is None


@pytest.mark.parametrize("mode", ["on_request", "catalog_only"])
def test_a_product_not_for_sale_may_have_no_price(mode: str) -> None:
    assert engine.price_required_error(mode, None) is None


def test_a_zero_price_is_refused_as_a_placeholder() -> None:
    errors = engine.price_errors(Decimal("0.00"), None, None, None)
    assert "price_net" in errors


def test_no_price_and_no_sale_is_consistent() -> None:
    assert engine.price_errors(None, None, None, None) == {}


def test_a_sale_needs_a_regular_price() -> None:
    errors = engine.price_errors(None, Decimal("5.00"), None, None)
    assert "sale_price_net" in errors
