"""ORD.1: the pure cart rules (stock limits, purchasability, merge plan, vendor packages)."""

from decimal import Decimal

import pytest

from apps.cart import engine
from apps.cart.engine import LineKey, LineState, PricedLine

TRACK, ALWAYS_OPEN, FROM_SOURCE = "track", "always_open", "from_source"
IN_STOCK, OUT_OF_STOCK, ON_BACKORDER = "in_stock", "out_of_stock", "on_backorder"

A = LineKey(product_id=1)
B = LineKey(product_id=2)
B_RED = LineKey(product_id=2, variant_id=21)
B_BLUE = LineKey(product_id=2, variant_id=22)


def state(key: LineKey, quantity: int, price: str = "10.00") -> LineState:
    return LineState(key=key, quantity=quantity, added_price_net=Decimal(price))


def test_the_engine_constants_match_the_catalog_choices() -> None:
    from apps.catalog.models import Product

    assert engine.PUBLISHED == Product.Status.PUBLISHED
    assert engine.ALWAYS_OPEN == Product.StockPolicy.ALWAYS_OPEN
    assert engine.OUT_OF_STOCK == Product.StockStatus.OUT_OF_STOCK


# --- Stock and limits


@pytest.mark.parametrize(
    ("policy", "qty", "status", "expected"),
    [
        (TRACK, 7, IN_STOCK, 7),
        (TRACK, 7, OUT_OF_STOCK, 0),
        (TRACK, 0, IN_STOCK, 0),
        (FROM_SOURCE, 3, IN_STOCK, 3),
        (FROM_SOURCE, 3, OUT_OF_STOCK, 0),
        (ALWAYS_OPEN, 0, ON_BACKORDER, None),
        (ALWAYS_OPEN, 0, OUT_OF_STOCK, None),
    ],
)
def test_available_quantity_follows_the_stock_policy(
    policy: str, qty: int, status: str, expected: int | None
) -> None:
    assert engine.available_quantity(policy, qty, status) == expected


def test_the_line_limit_is_the_stock_capped_by_the_per_line_maximum() -> None:
    assert engine.line_limit(5) == 5
    assert engine.line_limit(None) == engine.MAX_LINE_QUANTITY
    assert engine.line_limit(engine.MAX_LINE_QUANTITY + 50) == engine.MAX_LINE_QUANTITY
    assert engine.line_limit(-2) == 0


def test_quantity_error_explains_what_is_wrong() -> None:
    assert engine.quantity_error(3, limit=5) is None
    assert engine.quantity_error(5, limit=5) is None
    assert engine.quantity_error(0, limit=5) == "The quantity must be at least 1."
    assert engine.quantity_error(6, limit=5) == "Only 5 available."
    assert engine.quantity_error(1, limit=0) == "This item is out of stock."


# --- Purchasability


def test_a_published_buy_product_of_an_active_vendor_is_purchasable() -> None:
    assert (
        engine.purchase_error(
            product_status="published",
            sales_mode="buy",
            vendor_status="active",
            is_variable=False,
            has_variant=False,
        )
        is None
    )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"product_status": "draft"}, "This product is not for sale."),
        ({"product_status": "retired"}, "This product is not for sale."),
        ({"sales_mode": "on_request"}, "This product is available on request only."),
        ({"sales_mode": "catalog_only"}, "This product is available on request only."),
        ({"vendor_status": "suspended"}, "This vendor is not selling at the moment."),
        ({"is_variable": True}, "Choose a variant of this product."),
        ({"has_variant": True}, "This product has no variants."),
    ],
)
def test_anything_else_cannot_go_in_a_cart(overrides: dict[str, object], message: str) -> None:
    given: dict[str, object] = {
        "product_status": "published",
        "sales_mode": "buy",
        "vendor_status": "active",
        "is_variable": False,
        "has_variant": False,
        **overrides,
    }
    assert engine.purchase_error(**given) == message  # type: ignore[arg-type]


def test_a_variable_product_with_a_variant_is_purchasable() -> None:
    assert (
        engine.purchase_error(
            product_status="published",
            sales_mode="buy",
            vendor_status="active",
            is_variable=True,
            has_variant=True,
        )
        is None
    )


def test_display_price_uses_the_variant_override_and_hides_non_buy_prices() -> None:
    price = Decimal("10.00")
    assert engine.display_unit_price(price, "buy", None) == Decimal("10.00")
    assert engine.display_unit_price(price, "buy", Decimal("12.50")) == Decimal("12.50")
    assert engine.display_unit_price(price, "on_request", Decimal("12.50")) is None


# --- Merge on login (the rule is documented in engine.merge_lines)


def test_merge_adds_up_the_quantities_of_the_same_line() -> None:
    plan = engine.merge_lines([state(A, 2)], [state(A, 3)], limits={A: 10})
    assert [(m.key, m.quantity, m.requested, m.adjusted) for m in plan] == [(A, 5, 5, False)]


def test_merge_caps_the_sum_at_the_available_stock_and_reports_it() -> None:
    plan = engine.merge_lines([state(A, 4)], [state(A, 3)], limits={A: 5})
    (line,) = plan
    assert (line.quantity, line.requested, line.adjusted) == (5, 7, True)


def test_merge_keeps_the_price_the_customer_saw_last_in_the_guest_cart() -> None:
    plan = engine.merge_lines([state(A, 1, "9.00")], [state(A, 1, "11.00")], limits={A: 10})
    assert plan[0].added_price_net == Decimal("11.00")


def test_merge_moves_guest_only_lines_and_leaves_user_only_lines_alone() -> None:
    plan = engine.merge_lines([state(A, 2)], [state(B, 1)], limits={A: 10, B: 10})
    assert [(m.key, m.quantity) for m in plan] == [(B, 1)]


def test_merge_drops_a_guest_line_that_can_no_longer_be_bought() -> None:
    plan = engine.merge_lines([], [state(A, 2), state(B, 1)], limits={A: 0})
    assert [(m.key, m.quantity, m.adjusted) for m in plan] == [(A, 0, True), (B, 0, True)]


def test_merge_never_mixes_two_variants_of_the_same_product() -> None:
    plan = engine.merge_lines(
        [state(B_RED, 1)], [state(B_RED, 1), state(B_BLUE, 2)], limits={B_RED: 9, B_BLUE: 9}
    )
    assert [(m.key, m.quantity) for m in plan] == [(B_RED, 2), (B_BLUE, 2)]


# --- Packages per vendor (the multi-vendor split of ORD.3)


def priced(vendor_id: int, unit: str | None, qty: int, added: str = "10.00") -> PricedLine[str]:
    return PricedLine(
        item=f"line-{vendor_id}-{unit}",
        vendor_id=vendor_id,
        quantity=qty,
        unit_price_net=None if unit is None else Decimal(unit),
        added_price_net=Decimal(added),
    )


def test_lines_are_grouped_by_vendor_in_the_order_they_were_added() -> None:
    lines = [priced(7, "10.00", 1), priced(3, "2.50", 2), priced(7, "1.115", 3)]
    packages = engine.group_by_vendor(lines)
    assert [p.vendor_id for p in packages] == [7, 3]
    assert [len(p.lines) for p in packages] == [2, 1]
    # Rounded per line (ROUND_HALF_UP) before adding up: 10.00 + 3.35 (3 x 1.115 = 3.345).
    assert packages[0].goods_net == Decimal("13.35")
    assert packages[1].goods_net == Decimal("5.00")


def test_a_line_without_a_public_price_counts_nothing_and_is_flagged() -> None:
    line = priced(1, None, 2)
    assert line.line_net is None
    assert engine.group_by_vendor([line])[0].goods_net == Decimal("0.00")


def test_a_price_change_since_the_line_was_added_is_flagged() -> None:
    assert priced(1, "12.00", 1, added="10.00").price_changed is True
    assert priced(1, "10.00", 1, added="10.00").price_changed is False
