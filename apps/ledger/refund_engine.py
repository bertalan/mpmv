"""Pure refund arithmetic (PAY.4, D-41; no ORM, no I/O).

A vendor asks to refund by LINE (what is left of a line), by QUANTITY (some units of a line) or by
AMOUNT. The first two convert to a gross amount here; `apps.vendors.commission.refund_lines` then
takes that `refunded_gross` as a `RefundEvent`. Everything is `Decimal`, rounded `ROUND_HALF_UP`
per line; nothing here rounds up (the one ceil is the refund fee, in `apps.vendors.refund_fee`).
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from apps.core.money import round_line

MODE_LINE: Final = "line"
MODE_QUANTITY: Final = "quantity"
MODE_AMOUNT: Final = "amount"
MODES: Final = (MODE_LINE, MODE_QUANTITY, MODE_AMOUNT)
ZERO: Final = Decimal("0.00")


class InvalidRefundRequest(ValueError):
    """What was asked cannot be refunded (nothing selected, too much, a wrong mode)."""


@dataclass(frozen=True)
class LineFigures:
    """One order line as it was sold: its quantity and its gross (net + VAT), from the snapshot."""

    line_id: int
    quantity: int
    gross: Decimal


@dataclass(frozen=True)
class RefundSelection:
    """`mode` line: `lines` holds line ids (the quantity field is ignored: what is left of the
    line). `quantity`: line ids with the units to refund. `amount`: `amount` only."""

    mode: str
    lines: tuple[tuple[int, int], ...] = ()
    amount: Decimal | None = None

    def snapshot(self) -> dict[str, object]:
        """What the `Refund` row and the audit trail keep of the request."""
        stored: dict[str, object] = {
            "mode": self.mode,
            "lines": [{"line": line_id, "qty": qty} for line_id, qty in self.lines],
        }
        if self.amount is not None:
            stored["amount"] = str(self.amount)
        return stored

    def same_request_as(self, stored: Mapping[str, object]) -> bool:
        """Whether a stored request is this one (a repeat of the same request id): the same mode,
        amount and lines. A `line` request is stored with the units it resolved to, so only its
        line ids are compared."""
        mine = self.snapshot()
        if stored.get("mode") != mine["mode"] or stored.get("amount") != mine.get("amount"):
            return False
        ids = [entry["line"] for entry in stored.get("lines", [])]  # type: ignore[attr-defined]
        return sorted(ids) == sorted(line_id for line_id, _ in self.lines)


def unit_gross(figures: LineFigures, quantity: int) -> Decimal:
    """The gross of `quantity` units of a line: the whole gross for the whole line, else the
    line's gross pro rata, `ROUND_HALF_UP`."""
    if quantity == figures.quantity:
        return round_line(figures.gross)
    return round_line(figures.gross * quantity / figures.quantity)


def refunded_quantities(requests: Iterable[Mapping[str, object]]) -> dict[int, int]:
    """Units already refunded per line, summed over the stored `RefundSelection.snapshot()`s."""
    done: dict[int, int] = {}
    for stored in requests:
        for entry in stored.get("lines", []):  # type: ignore[attr-defined]
            line_id = int(entry["line"])
            done[line_id] = done.get(line_id, 0) + int(entry["qty"])
    return done


def refunded_gross_of(
    selection: RefundSelection,
    lines: Mapping[int, LineFigures],
    already: Mapping[int, int],
) -> Decimal:
    """The gross amount the selection refunds. Raises `InvalidRefundRequest` for an unknown mode,
    an empty or repeated selection, a line of another order, more units than are left, or a
    non-positive amount or one with more than two decimals."""
    if selection.mode not in MODES:
        raise InvalidRefundRequest(f"unknown refund mode {selection.mode!r}")
    if selection.mode == MODE_AMOUNT:
        amount = selection.amount
        if selection.lines or amount is None or amount <= 0:
            raise InvalidRefundRequest("an amount refund needs a positive amount and no lines")
        if amount != round_line(amount):
            raise InvalidRefundRequest("an amount has at most two decimals")
        return amount
    if selection.amount is not None or not selection.lines:
        raise InvalidRefundRequest("a line or quantity refund needs lines and no amount")
    seen: set[int] = set()
    total = ZERO
    for line_id, asked in selection.lines:
        if line_id in seen:
            raise InvalidRefundRequest("a line can be listed once")
        seen.add(line_id)
        if line_id not in lines:
            raise InvalidRefundRequest("a line does not belong to this order")
        figures = lines[line_id]
        left = figures.quantity - already.get(line_id, 0)
        quantity = left if selection.mode == MODE_LINE else asked
        if quantity < 1 or quantity > left:
            raise InvalidRefundRequest(f"line {line_id}: {left} unit(s) left, asked {quantity}")
        total += unit_gross(figures, quantity)
    return round_line(total)


def normalised_lines(
    selection: RefundSelection, lines: Mapping[int, LineFigures], already: Mapping[int, int]
) -> RefundSelection:
    """The selection with the real quantity on every line (what a `line` refund resolved to), so
    the stored request counts the units correctly next time."""
    if selection.mode != MODE_LINE:
        return selection
    resolved = tuple(
        (line_id, lines[line_id].quantity - already.get(line_id, 0))
        for line_id, _ in selection.lines
    )
    return RefundSelection(MODE_LINE, resolved, None)
