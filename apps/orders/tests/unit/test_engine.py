"""ORD.2: the pure order rules (numbering format, line amounts, state machines)."""

from decimal import Decimal

import pytest

from apps.orders import engine
from apps.orders.engine import IllegalTransition


def test_the_number_is_year_dash_six_digit_sequence() -> None:
    assert engine.format_order_number(2027, 123) == "2027-000123"
    assert engine.format_order_number(2027, 1, prefix="MP-") == "MP-2027-000001"
    assert engine.format_order_number(2027, 1_234_567) == "2027-1234567"  # widens, never wraps


def test_the_number_round_trips() -> None:
    assert engine.parse_order_number("2027-000123") == ("", 2027, 123)
    assert engine.parse_order_number("MP-2027-000123") == ("MP-", 2027, 123)
    with pytest.raises(ValueError, match="not an order number"):
        engine.parse_order_number("nonsense")


def test_the_sequence_must_be_positive() -> None:
    with pytest.raises(ValueError, match="sequence"):
        engine.format_order_number(2027, 0)


def test_line_amounts_round_half_up_per_line() -> None:
    amounts = engine.line_amounts(Decimal("0.335"), 3, Decimal("0.00"), Decimal("22"))
    # net 1.005 -> 1.01 ; tax 1.01 * 22% = 0.2222 -> 0.22
    assert amounts.net == Decimal("1.01")
    assert amounts.tax == Decimal("0.22")
    assert amounts.gross == Decimal("1.23")


def test_line_amounts_apply_the_discount_before_tax() -> None:
    amounts = engine.line_amounts(Decimal("10.00"), 2, Decimal("5.00"), Decimal("10"))
    assert (amounts.net, amounts.tax, amounts.gross) == (
        Decimal("15.00"),
        Decimal("1.50"),
        Decimal("16.50"),
    )


def test_line_amounts_refuse_bad_input() -> None:
    with pytest.raises(ValueError, match="quantity"):
        engine.line_amounts(Decimal("1.00"), 0, Decimal("0"), Decimal("0"))
    with pytest.raises(ValueError, match="discount"):
        engine.line_amounts(Decimal("1.00"), 1, Decimal("2.00"), Decimal("0"))
    with pytest.raises(TypeError):
        engine.line_amounts(1.5, 1, Decimal("0"), Decimal("0"))  # type: ignore[arg-type]


def test_totals_add_up_the_rounded_lines() -> None:
    lines = [
        engine.line_amounts(Decimal("0.335"), 3, Decimal("0"), Decimal("22")),
        engine.line_amounts(Decimal("10.00"), 1, Decimal("0"), Decimal("22")),
    ]
    totals = engine.totals(lines, shipping_net=Decimal("5.00"), shipping_tax=Decimal("1.10"))
    assert totals.goods_net == Decimal("11.01")
    assert totals.shipping_net == Decimal("5.00")
    assert totals.tax_total == Decimal("0.22") + Decimal("2.20") + Decimal("1.10")
    assert totals.grand_total == totals.goods_net + totals.shipping_net + totals.tax_total


ORDER_FLOW = [
    ("checkout", "awaiting_payment"),
    ("checkout", "quote_flow"),
    ("quote_flow", "awaiting_payment"),
    ("awaiting_payment", "paid"),
    ("awaiting_payment", "cancelled"),
    ("paid", "in_fulfillment"),
    ("in_fulfillment", "fulfilled"),
    ("fulfilled", "closed"),
    ("paid", "partially_refunded"),
    ("fulfilled", "partially_refunded"),
    ("partially_refunded", "refunded"),
]


@pytest.mark.parametrize(("source", "target"), ORDER_FLOW)
def test_every_arrow_of_the_order_diagram_is_allowed(source: str, target: str) -> None:
    engine.check_order_transition(source, target)


def test_every_other_order_transition_is_illegal() -> None:
    allowed = set(ORDER_FLOW)
    for source in engine.ORDER_STATES:
        for target in engine.ORDER_STATES:
            if (source, target) not in allowed:
                with pytest.raises(IllegalTransition):
                    engine.check_order_transition(source, target)


def test_terminal_order_states_have_no_exit() -> None:
    for state in ("cancelled", "closed", "refunded"):
        assert engine.ORDER_TRANSITIONS[state] == frozenset()


def test_vendor_order_happy_path_and_exits() -> None:
    for source, target in [
        ("pending", "accepted"),
        ("accepted", "shipped"),
        ("shipped", "delivered"),
        ("delivered", "completed"),
        ("pending", "cancelled"),
        ("accepted", "cancelled"),
        ("accepted", "refunded"),
        ("delivered", "refunded"),
    ]:
        engine.check_vendor_order_transition(source, target)
    for source, target in [
        ("pending", "shipped"),
        ("shipped", "pending"),
        ("completed", "accepted"),
        ("cancelled", "pending"),
        ("refunded", "completed"),
        ("pending", "pending"),
    ]:
        with pytest.raises(IllegalTransition):
            engine.check_vendor_order_transition(source, target)


def test_unknown_states_are_illegal() -> None:
    with pytest.raises(IllegalTransition):
        engine.check_order_transition("paid", "teleported")
    with pytest.raises(IllegalTransition):
        engine.check_vendor_order_transition("bogus", "accepted")


@pytest.mark.parametrize(
    ("current", "vendor_states", "expected"),
    [
        ("paid", ["pending", "pending"], "paid"),
        ("paid", ["accepted", "pending"], "in_fulfillment"),
        ("in_fulfillment", ["shipped", "accepted"], "in_fulfillment"),
        ("in_fulfillment", ["shipped", "delivered"], "fulfilled"),
        ("in_fulfillment", ["completed", "cancelled"], "fulfilled"),
        ("in_fulfillment", ["cancelled", "cancelled"], "in_fulfillment"),
        ("awaiting_payment", ["accepted"], "awaiting_payment"),
        ("fulfilled", ["completed"], "fulfilled"),
    ],
)
def test_the_order_state_is_derived_from_its_vendor_orders(
    current: str, vendor_states: list[str], expected: str
) -> None:
    assert engine.derive_order_state(current, vendor_states) == expected
