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
    ("in_fulfillment", "partially_refunded"),
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


def test_a_refunded_vendor_order_does_not_fulfil_or_block_the_others() -> None:
    # One vendor refunded while another is still being fulfilled: no derived change.
    assert engine.derive_order_state("partially_refunded", ["refunded", "accepted"]) == (
        "partially_refunded"
    )
    assert engine.derive_order_state("in_fulfillment", ["refunded", "shipped"]) == "fulfilled"


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


def test_a_derived_state_is_always_reachable_in_one_legal_step() -> None:
    import itertools

    for current in engine.ORDER_STATES:
        for combo in itertools.chain.from_iterable(
            itertools.product(engine.VENDOR_ORDER_STATES, repeat=n) for n in (1, 2)
        ):
            derived = engine.derive_order_state(current, combo)
            if derived != current:
                engine.check_order_transition(current, derived)


def test_a_paid_order_steps_through_in_fulfillment() -> None:
    assert engine.derive_order_state("paid", ["shipped"]) == "in_fulfillment"


def test_shipping_tax_is_rounded_half_up() -> None:
    assert engine.shipping_tax(Decimal("5.00"), Decimal("22")) == Decimal("1.10")
    assert engine.shipping_tax(Decimal("0.25"), Decimal("22")) == Decimal("0.06")  # 0.055
    with pytest.raises(ValueError, match="shipping"):
        engine.shipping_tax(Decimal("-1"), Decimal("22"))


def test_add_months_clamps_the_day() -> None:
    import datetime as dt

    assert engine.add_months(dt.date(2027, 1, 31), 1) == dt.date(2027, 2, 28)
    assert engine.add_months(dt.date(2027, 3, 15), 24) == dt.date(2029, 3, 15)


def test_minimisation_is_due_after_the_active_phase() -> None:
    import datetime as dt
    import zoneinfo

    rome = zoneinfo.ZoneInfo("Europe/Rome")
    created = dt.datetime(2027, 3, 15, 10, 0, tzinfo=dt.UTC)
    assert not engine.minimisation_due(
        created, dt.datetime(2029, 3, 14, 12, tzinfo=dt.UTC), 24, rome
    )
    assert engine.minimisation_due(created, dt.datetime(2029, 3, 15, 12, tzinfo=dt.UTC), 24, rome)


def test_anonymisation_is_due_after_31_december_of_year_ten() -> None:
    import datetime as dt
    import zoneinfo

    rome = zoneinfo.ZoneInfo("Europe/Rome")
    created = dt.datetime(2027, 3, 15, tzinfo=dt.UTC)
    # 2037-12-31 22:30 UTC is still 31 Dec in Rome; 23:30 UTC is 00:30 on 1 Jan 2038.
    assert not engine.anonymisation_due(
        created, dt.datetime(2037, 12, 31, 22, 30, tzinfo=dt.UTC), rome
    )
    assert engine.anonymisation_due(created, dt.datetime(2037, 12, 31, 23, 30, tzinfo=dt.UTC), rome)
    # An order placed at 23:30 UTC on 31 Dec 2027 belongs to 2028 in Rome.
    late = dt.datetime(2027, 12, 31, 23, 30, tzinfo=dt.UTC)
    assert not engine.anonymisation_due(late, dt.datetime(2038, 6, 1, tzinfo=dt.UTC), rome)
    assert engine.anonymisation_due(late, dt.datetime(2039, 1, 1, tzinfo=dt.UTC), rome)
