"""PAY.4 (D-41): a refund by line or quantity converts to `refunded_gross` (pure, no database)."""

from decimal import Decimal

import pytest

from apps.ledger import refund_engine as engine
from apps.ledger.refund_engine import LineFigures, RefundSelection

D = Decimal
LINES = {
    1: LineFigures(1, 3, D("36.60")),  # 3 x 10.00 net + 22% VAT
    2: LineFigures(2, 1, D("12.20")),
    3: LineFigures(3, 3, D("10.00")),  # 3.3333 per unit: rounding matters
}


def gross(selection: RefundSelection, already: dict[int, int] | None = None) -> Decimal:
    return engine.refunded_gross_of(selection, LINES, already or {})


def test_a_line_refund_is_the_whole_line_gross() -> None:
    assert gross(RefundSelection("line", ((2, 1),))) == D("12.20")
    assert gross(RefundSelection("line", ((1, 3), (2, 1)))) == D("48.80")


def test_a_quantity_refund_is_pro_rata_half_up_per_line() -> None:
    assert gross(RefundSelection("quantity", ((1, 1),))) == D("12.20")
    assert gross(RefundSelection("quantity", ((3, 1),))) == D("3.33")
    assert gross(RefundSelection("quantity", ((3, 2),))) == D("6.67")  # 6.6666 up
    assert gross(RefundSelection("quantity", ((3, 3),))) == D("10.00")  # whole line: exact


def test_the_total_is_the_sum_of_the_rounded_lines() -> None:
    assert gross(RefundSelection("quantity", ((3, 1), (1, 1)))) == D("15.53")


def test_a_line_refund_takes_what_is_left_of_the_line() -> None:
    assert gross(RefundSelection("line", ((1, 3),)), {1: 2}) == D("12.20")
    normal = engine.normalised_lines(RefundSelection("line", ((1, 3),)), LINES, {1: 2})
    assert normal.lines == ((1, 1),)


def test_an_amount_refund_is_the_amount() -> None:
    assert gross(RefundSelection("amount", amount=D("61.00"))) == D("61.00")


@pytest.mark.parametrize(
    "selection",
    [
        RefundSelection("amount"),
        RefundSelection("amount", amount=D("0")),
        RefundSelection("amount", amount=D("-1.00")),
        RefundSelection("amount", amount=D("1.005")),
        RefundSelection("amount", ((1, 1),), D("5.00")),
        RefundSelection("quantity"),
        RefundSelection("quantity", ((1, 0),)),
        RefundSelection("quantity", ((1, 4),)),
        RefundSelection("quantity", ((9, 1),)),
        RefundSelection("quantity", ((1, 1), (1, 1))),
        RefundSelection("line", ((2, 1),), D("5.00")),
        RefundSelection("everything"),
    ],
)
def test_a_wrong_request_is_refused(selection: RefundSelection) -> None:
    with pytest.raises(engine.InvalidRefundRequest):
        gross(selection)


def test_more_units_than_are_left_is_refused() -> None:
    with pytest.raises(engine.InvalidRefundRequest):
        gross(RefundSelection("quantity", ((1, 2),)), {1: 2})
    with pytest.raises(engine.InvalidRefundRequest):
        gross(RefundSelection("line", ((1, 3),)), {1: 3})


def test_already_refunded_quantities_add_up_over_stored_requests() -> None:
    stored = [
        RefundSelection("quantity", ((1, 1),)).snapshot(),
        RefundSelection("quantity", ((1, 1), (3, 2)), None).snapshot(),
        RefundSelection("amount", amount=D("1.00")).snapshot(),
    ]
    assert engine.refunded_quantities(stored) == {1: 2, 3: 2}
