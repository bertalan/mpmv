"""Pure order rules (spec 07 §7.2, ORD.2): numbering, line amounts, state machines. No ORM.

Amounts are `Decimal`, rounded half up per line before they are added up (spec 01 money rules).
"""

import calendar
import datetime as dt
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import quote

from apps.core.money import round_line
from apps.tax.engine import (
    ShippingCharge,
    TaxContext,
    TaxDecision,
    TaxPortion,
    resolve_shipping_tax,
)

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


# --- checkout preview (ORD.3, SHP.4) ---------------------------------------------------------

# Blocking messages of the checkout. `needs_quote` is not a failure: the package goes the quote
# way (SHP.9) and the order cannot be placed as a plain one.
CHECKOUT_OFF = "checkout_disabled"
EMPTY_CART = "empty_cart"
ADDRESS_MISSING = "address_missing"
NOT_FOR_SALE = "not_for_sale"
ON_REQUEST = "on_request"
OUT_OF_STOCK = "out_of_stock"
NEEDS_QUOTE = "needs_quote"
SHIPPING_BLOCKED = "shipping_blocked"
SHIPPING_CHOICE_INVALID = "shipping_choice_invalid"
TAX_BLOCKED = "tax_blocked"
NO_PRICE = "no_price"
SHIPPING_VAT_SPLIT = "shipping_vat_split"

READY = "ready"
QUOTE = "quote"
BLOCKED = "blocked"

REQUIRED_ADDRESS_FIELDS = ("line1", "city", "postcode", "country")


@dataclass(frozen=True)
class Block:
    """Why the checkout cannot go on (or must take the quote way), for one vendor package or the
    whole cart (`vendor_id` None). `fields` names the missing address fields."""

    code: str
    message: str
    vendor_id: int | None = None
    product_id: int | None = None
    fields: tuple[str, ...] = ()


def missing_address_fields(address: Mapping[str, Any]) -> list[str]:
    """The required address fields that are absent or blank, in a fixed order."""
    return [name for name in REQUIRED_ADDRESS_FIELDS if not str(address.get(name) or "").strip()]


@dataclass(frozen=True)
class PackageLineInput:
    """One line to price: the unit price the customer sees, the discount and the goods tax
    decision (`resolve_goods_tax`)."""

    unit_price_net: Decimal
    quantity: int
    discount_net: Decimal
    decision: TaxDecision


@dataclass(frozen=True)
class PricedLine:
    amounts: LineAmounts
    decision: TaxDecision


@dataclass(frozen=True)
class PackageAmounts:
    """A vendor package priced: lines, the shipping split over the goods' decisions (TAX.17,
    pro rata, remainder on the largest share) and the totals."""

    lines: tuple[PricedLine, ...]
    shipping_portions: tuple[TaxPortion, ...]
    shipping_tax: Decimal
    totals: Totals


@dataclass(frozen=True)
class SummaryLine:
    product_id: int
    variant_id: int | None
    title: str
    quantity: int
    unit_price_net: Decimal
    net: Decimal | None = None
    tax_rate: Decimal | None = None
    tax: Decimal | None = None


@dataclass(frozen=True)
class ShippingOption:
    """A shipping choice offered for a package, net of VAT."""

    key: str
    kind: str
    label: str
    net: Decimal
    is_default: bool = False
    min_days: int | None = None
    max_days: int | None = None


@dataclass(frozen=True)
class PackageSummary:
    """One vendor's package as the checkout shows it. `state` is `ready` (priced and placeable),
    `quote` (a shipping quote is needed first) or `blocked`. Amounts are set only when ready."""

    vendor_id: int
    state: str
    lines: tuple[SummaryLine, ...]
    blocks: tuple[Block, ...] = ()
    shipping_options: tuple[ShippingOption, ...] = ()
    selected_shipping: str = ""
    shipping_portions: tuple[TaxPortion, ...] = ()
    totals: Totals | None = None


def price_package(
    ctx: TaxContext, lines: Sequence[PackageLineInput], shipping_net: Decimal
) -> PackageAmounts:
    """Each line to cents (ROUND_HALF_UP), the shipping VAT through the tax engine, the totals.

    Shipping follows the goods: with two VAT rates in the package the shipping has two portions.
    A package without shipping charge (0.00, pickup or free) has no portions.
    """
    if not lines:
        raise ValueError("a package needs at least one line")
    priced = tuple(
        PricedLine(
            line_amounts(
                entry.unit_price_net, entry.quantity, entry.discount_net, entry.decision.rate
            ),
            entry.decision,
        )
        for entry in lines
    )
    portions = tuple(
        resolve_shipping_tax(
            ctx, [(p.amounts.net, p.decision) for p in priced], ShippingCharge(net=shipping_net)
        )
    )
    shipping_tax = sum((p.tax for p in portions), start=Decimal("0.00"))
    return PackageAmounts(
        lines=priced,
        shipping_portions=portions,
        shipping_tax=shipping_tax,
        totals=totals(
            (p.amounts for p in priced), shipping_net=shipping_net, shipping_tax=shipping_tax
        ),
    )


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


# --- the vendor's fulfilment actions (ORD.6) ---------------------------------------------------

FULFILMENT_ACTIONS: Mapping[str, str] = {
    "accept": "accepted",
    "ship": "shipped",
    "deliver": "delivered",
}

TRACKING_MIN, TRACKING_MAX, CARRIER_MAX = 3, 100, 100
_TRACKING_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ./_-]*")
_CARRIER_RE = re.compile(r"[\w][\w .,&'()/-]*")


class UnknownFulfilmentAction(ValueError):
    """The action is not one of `FULFILMENT_ACTIONS`."""


class InvalidTracking(ValueError):
    """A tracking number or carrier name that cannot be right."""


def fulfilment_target(action: str) -> str:
    try:
        return FULFILMENT_ACTIONS[action]
    except KeyError:
        raise UnknownFulfilmentAction(f"unknown fulfilment action {action!r}") from None


def normalise_tracking(raw: str) -> str:
    """The trimmed tracking number: letters, digits, space and `. / _ -`, 3 to 100 characters."""
    value = raw.strip()
    if not TRACKING_MIN <= len(value) <= TRACKING_MAX or not _TRACKING_RE.fullmatch(value):
        raise InvalidTracking("the tracking number is missing or has characters it cannot have")
    return value


def normalise_carrier(raw: str) -> str:
    """The trimmed carrier name; optional."""
    value = raw.strip()
    if value and (len(value) > CARRIER_MAX or not _CARRIER_RE.fullmatch(value)):
        raise InvalidTracking("the carrier name is too long or has characters it cannot have")
    return value


# --- the customer's view of one vendor part (ORD.7) -------------------------------------------


@dataclass(frozen=True)
class TimelineStep:
    """One step of the status timeline. `status` is `done`, `current` (the last step reached)
    or `upcoming`; `at` is the time when the order keeps one for the step."""

    key: str
    status: str
    at: dt.datetime | None


_TIMELINE_KEYS = ("placed", "paid", "accepted", "shipped", "delivered")
_ACCEPTED_ON = frozenset({"accepted", "shipped", "delivered", "completed"})
_SHIPPED_ON = frozenset({"shipped", "delivered", "completed"})
_DELIVERED_ON = frozenset({"delivered", "completed"})


def vendor_timeline(
    state: str,
    *,
    placed_at: dt.datetime,
    paid_at: dt.datetime | None,
    shipped_at: dt.datetime | None,
    delivered_at: dt.datetime | None,
) -> tuple[TimelineStep, ...]:
    """The steps a customer sees for one vendor part: placed, paid, accepted, shipped, delivered.

    A cancelled or refunded part keeps the steps its timestamps prove and ends with one step
    named after its state (the order does not keep the time the vendor accepted a part)."""
    dead = state in _DEAD_STATES
    reached = {
        "placed": True,
        "paid": paid_at is not None,
        "accepted": shipped_at is not None or (not dead and state in _ACCEPTED_ON),
        "shipped": shipped_at is not None or (not dead and state in _SHIPPED_ON),
        "delivered": delivered_at is not None or (not dead and state in _DELIVERED_ON),
    }
    times: dict[str, dt.datetime | None] = {
        "placed": placed_at,
        "paid": paid_at,
        "accepted": None,
        "shipped": shipped_at,
        "delivered": delivered_at,
    }
    done = [key for key in _TIMELINE_KEYS if reached[key]]
    if dead:
        return (
            *(TimelineStep(key, "done", times[key]) for key in done),
            TimelineStep(state, "current", None),
        )
    return tuple(
        TimelineStep(
            key,
            "upcoming" if not reached[key] else ("current" if key == done[-1] else "done"),
            times[key],
        )
        for key in _TIMELINE_KEYS
    )


def tracking_link(template: str, tracking_number: str) -> str:
    """The carrier's tracking page for a number, or "" when there is none to show.

    The template comes from the carrier record and must hold `{tracking_number}` and start with
    `https://`; the number is percent-encoded. Anything else gives no link, never a bad one."""
    if not tracking_number or "{tracking_number}" not in template:
        return ""
    if not template.startswith("https://"):
        return ""
    return template.replace("{tracking_number}", quote(tracking_number, safe=""))


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
# Frozen into the database trigger (migration 0006): the setting ORDER_ACTIVE_PHASE_MONTHS can
# only make the active phase longer than this.
MIN_ACTIVE_PHASE_MONTHS = 24
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


def restriction_due(created: dt.datetime, now: dt.datetime, months: int, tz: dt.tzinfo) -> bool:
    """True once the active phase (`months` after the order was placed) is over."""
    due = add_months(created.astimezone(tz).date(), months)
    return now.astimezone(tz).date() >= due


def tax_retention_until(created: dt.datetime, tz: dt.tzinfo) -> dt.date:
    """31 December of (the order's calendar year in `tz` + 10): the fiscal retention date."""
    return dt.date(created.astimezone(tz).year + FISCAL_RETENTION_YEARS, 12, 31)


def retention_over(until: dt.date, today: dt.date) -> bool:
    """Anonymisation of a paid order runs from the DAY AFTER the retention date (D-31)."""
    return today > until


def anonymisation_due(created: dt.datetime, now: dt.datetime, tz: dt.tzinfo) -> bool:
    return retention_over(tax_retention_until(created, tz), now.astimezone(tz).date())


# Privacy lifecycle (D-30): forward only. An order never paid may go straight to anonymized.
PRIVACY_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "active": frozenset({"restricted_tax_only", "anonymized"}),
    "restricted_tax_only": frozenset({"anonymized"}),
    "anonymized": frozenset(),
}
# An order that never reached `paid` may be anonymised at once when it is `cancelled` (expired or
# failed checkouts end there) or when a draft / awaiting-payment checkout has been abandoned for
# this many days (frozen into the database trigger, migration 0009). A live checkout is never
# anonymised: a payment can still arrive. `paid_at` is set by the trigger when an order first
# becomes paid, so "never paid" does not depend on the current state.
ABANDONED_AFTER_DAYS = 30
_ABANDONABLE = frozenset({"checkout", "quote_flow", "awaiting_payment"})


def anonymisable_at_once(
    state: str, paid_at: dt.datetime | None, created: dt.datetime, now: dt.datetime
) -> bool:
    if paid_at is not None:
        return False
    if state == "cancelled":
        return True
    return state in _ABANDONABLE and now - created > dt.timedelta(days=ABANDONED_AFTER_DAYS)


def check_privacy_transition(source: str, target: str) -> None:
    _check(PRIVACY_TRANSITIONS, "an order's privacy status", source, target)
