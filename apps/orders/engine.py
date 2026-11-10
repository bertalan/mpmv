"""Pure order rules (spec 07 §7.2, ORD.2): numbering, line amounts, state machines. No ORM.

Amounts are `Decimal`, rounded half up per line before they are added up (spec 01 money rules).
"""

import calendar
import datetime as dt
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal

from apps.core.money import round_line

HUNDRED = Decimal(100)

# --- numbering -------------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"^(?P<prefix>.*?)(?P<year>\d{4})-(?P<sequence>\d{6,})$")


def format_order_number(year: int, sequence: int, *, prefix: str = "") -> str:
    """`2027-000123`, or `MP-2027-000123` with a prefix. The number is an order reference,
    never an invoice number: the vendor invoices (spec 00)."""
    if sequence < 1:
        raise ValueError("the sequence starts at 1")
    return f"{prefix}{year:04d}-{sequence:06d}"


def parse_order_number(number: str) -> tuple[str, int, int]:
    """`(prefix, year, sequence)` of a number made by `format_order_number`."""
    match = _NUMBER_RE.match(number)
    if match is None:
        raise ValueError(f"{number!r} is not an order number")
    return match["prefix"], int(match["year"]), int(match["sequence"])


# --- amounts ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class LineAmounts:
    net: Decimal  # after the discount
    tax: Decimal
    gross: Decimal


def line_amounts(
    unit_price_net: Decimal, quantity: int, discount_net: Decimal, tax_rate: Decimal
) -> LineAmounts:
    """Net (unit price x quantity - discount) and tax of one line, each rounded to cents."""
    for value in (unit_price_net, discount_net, tax_rate):
        if not isinstance(value, Decimal):
            raise TypeError("amounts must be Decimal")
    if quantity < 1:
        raise ValueError("the quantity must be at least 1")
    if discount_net < 0:
        raise ValueError("the discount cannot be negative")
    net = round_line(unit_price_net * quantity - discount_net)
    if net < 0:
        raise ValueError("the discount cannot exceed the line amount")
    tax = round_line(net * tax_rate / HUNDRED)
    return LineAmounts(net=net, tax=tax, gross=net + tax)


@dataclass(frozen=True)
class Totals:
    goods_net: Decimal
    shipping_net: Decimal
    tax_total: Decimal
    grand_total: Decimal


def totals(lines: Iterable[LineAmounts], *, shipping_net: Decimal, shipping_tax: Decimal) -> Totals:
    """Goods net, shipping, total tax (lines + shipping) and the grand total."""
    items = list(lines)
    goods_net = sum((line.net for line in items), start=Decimal("0.00"))
    tax_total = sum((line.tax for line in items), start=Decimal("0.00")) + shipping_tax
    return Totals(
        goods_net=goods_net,
        shipping_net=shipping_net,
        tax_total=tax_total,
        grand_total=goods_net + shipping_net + tax_total,
    )


def add_totals(parts: Iterable[Totals]) -> Totals:
    items = list(parts)
    zero = Decimal("0.00")
    return Totals(
        goods_net=sum((p.goods_net for p in items), start=zero),
        shipping_net=sum((p.shipping_net for p in items), start=zero),
        tax_total=sum((p.tax_total for p in items), start=zero),
        grand_total=sum((p.grand_total for p in items), start=zero),
    )


def shipping_tax(shipping_net: Decimal, rate: Decimal) -> Decimal:
    """The tax on the shipping of one vendor order, rounded half up to cents."""
    if shipping_net < 0:
        raise ValueError("shipping cannot be negative")
    return round_line(shipping_net * rate / HUNDRED)


# --- state machines --------------------------------------------------------------------------


class IllegalTransition(ValueError):
    """A state change the machine does not have an arrow for."""


ORDER_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "checkout": frozenset({"awaiting_payment", "quote_flow"}),
    "quote_flow": frozenset({"awaiting_payment"}),
    "awaiting_payment": frozenset({"paid", "cancelled"}),
    "paid": frozenset({"in_fulfillment", "partially_refunded"}),
    "in_fulfillment": frozenset({"fulfilled", "partially_refunded"}),
    "fulfilled": frozenset({"closed", "partially_refunded"}),
    "partially_refunded": frozenset({"refunded"}),
    "closed": frozenset(),
    "cancelled": frozenset(),
    "refunded": frozenset(),
}
ORDER_STATES: tuple[str, ...] = tuple(ORDER_TRANSITIONS)
ORDER_INITIAL_STATES = frozenset({"checkout", "quote_flow", "awaiting_payment"})

VENDOR_ORDER_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "pending": frozenset({"accepted", "cancelled"}),
    "accepted": frozenset({"shipped", "cancelled", "refunded"}),
    "shipped": frozenset({"delivered", "refunded"}),
    "delivered": frozenset({"completed", "refunded"}),
    "completed": frozenset({"refunded"}),
    "cancelled": frozenset(),
    "refunded": frozenset(),
}
VENDOR_ORDER_STATES: tuple[str, ...] = tuple(VENDOR_ORDER_TRANSITIONS)
VENDOR_ORDER_INITIAL_STATES = frozenset({"pending"})


def _check(table: Mapping[str, frozenset[str]], kind: str, source: str, target: str) -> None:
    if source not in table or target not in table[source]:
        raise IllegalTransition(f"{kind} cannot go from {source!r} to {target!r}")


def check_order_transition(source: str, target: str) -> None:
    _check(ORDER_TRANSITIONS, "an order", source, target)


def check_vendor_order_transition(source: str, target: str) -> None:
    _check(VENDOR_ORDER_TRANSITIONS, "a vendor order", source, target)


_FULFILLED_STATES = frozenset({"shipped", "delivered", "completed"})
_STARTED_STATES = frozenset({"accepted"}) | _FULFILLED_STATES
_DEAD_STATES = frozenset({"cancelled", "refunded"})


def derive_order_state(current: str, vendor_states: Iterable[str]) -> str:
    """The next order state implied by its vendor orders (spec 07: derived from the sub-orders).

    One legal step at a time, so `check_order_transition(current, result)` always accepts the
    answer (call again until it is stable): a paid order moves to `in_fulfillment` when a vendor
    accepts; an `in_fulfillment` order moves to `fulfilled` when every vendor order that is
    still alive has shipped or been delivered. Payment, refund and closing states are never
    derived.
    """
    alive = [state for state in vendor_states if state not in _DEAD_STATES]
    if not alive:
        return current
    if current == "paid" and any(state in _STARTED_STATES for state in alive):
        return "in_fulfillment"
    if current == "in_fulfillment" and all(state in _FULFILLED_STATES for state in alive):
        return "fulfilled"
    return current


# --- retention (owner decision D-29) -----------------------------------------------------------

ANONYMISED = "ANONYMISED"
ANONYMISED_EMAIL = "anonymised@example.invalid"
FISCAL_RETENTION_YEARS = 10
ANONYMISED_ORDER_FIELDS: Mapping[str, object] = {
    "customer_name": ANONYMISED,
    "customer_vat_number": ANONYMISED,
    "customer_tax_id": ANONYMISED,
    "billing_address": {"anonymised": True},
    "shipping_address": {"anonymised": True},
    "vat_validation": {"anonymised": True},
}


def add_months(day: dt.date, months: int) -> dt.date:
    """`day` plus whole months; the day is clamped to the end of a shorter month."""
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    month += 1
    return dt.date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def minimisation_due(created: dt.datetime, now: dt.datetime, months: int, tz: dt.tzinfo) -> bool:
    """True once the active phase (`months` after the order was placed) is over."""
    due = add_months(created.astimezone(tz).date(), months)
    return now.astimezone(tz).date() >= due


def anonymisation_due(created: dt.datetime, now: dt.datetime, tz: dt.tzinfo) -> bool:
    """True after 31 December of year 10 counted from the order's calendar year."""
    last_day = dt.date(created.astimezone(tz).year + FISCAL_RETENTION_YEARS, 12, 31)
    return now.astimezone(tz).date() > last_day
