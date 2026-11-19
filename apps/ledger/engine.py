"""Pure ledger rules (VEN.4, spec 04): kinds, sign per kind, balance and statement arithmetic.

No ORM, no clock. The ledger is an operational record of what each vendor is owed and paid, not
accounting (spec 04): amounts are signed from the vendor's side, positive for a credit to the
vendor and negative for a debit, always in EUR, rounded `ROUND_HALF_UP` to the cent.
"""

import datetime as dt
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
CURRENCY = "EUR"

SALE = "sale"
SHIPPING = "shipping"
COMMISSION = "commission"
PAYMENT_FEE = "payment_fee"
REFUND = "refund"
COMMISSION_REVERSAL = "commission_reversal"
PAYMENT_FEE_REVERSAL = "payment_fee_reversal"
TRANSFER_REVERSAL = "transfer_reversal"
REFUND_FEE = "refund_fee"
ADJUSTMENT = "adjustment"
PAYOUT = "payout"
TRANSFER = "transfer"
PLAN_FEE = "plan_fee"
BUNDLE_DISCOUNT_VENDOR_FUNDED = "bundle_discount_vendor_funded"
BUNDLE_DISCOUNT_PLATFORM_FUNDED = "bundle_discount_platform_funded"

# Money the vendor is owed: the sale and shipping it collected, the commission and the collection
# cost given back on a refund (PAY.4), a transfer taken back from its account by a refund (the
# balance the platform owes it rises again by what the provider took back), and a discount the
# platform funds.
CREDIT_KINDS = frozenset(
    {
        SALE,
        SHIPPING,
        COMMISSION_REVERSAL,
        PAYMENT_FEE_REVERSAL,
        TRANSFER_REVERSAL,
        BUNDLE_DISCOUNT_PLATFORM_FUNDED,
    }
)
# Money the vendor owes or was paid: fees, refunds, discounts it funds, and what was sent to it.
DEBIT_KINDS = frozenset(
    {
        COMMISSION,
        PAYMENT_FEE,
        REFUND,
        REFUND_FEE,
        PAYOUT,
        TRANSFER,
        PLAN_FEE,
        BUNDLE_DISCOUNT_VENDOR_FUNDED,
    }
)
KINDS = CREDIT_KINDS | DEBIT_KINDS | {ADJUSTMENT}


class LedgerError(Exception):
    """Base class of the ledger refusals."""


class InvalidEntry(LedgerError):
    """The kind, amount, currency or references of an entry are not acceptable."""


class IdempotencyConflict(LedgerError):
    """The idempotency key is already used by an entry with different content."""


def money(value: Decimal) -> Decimal:
    """`value` rounded half up to the cent. Floats are refused."""
    if isinstance(value, float):
        raise TypeError("float amounts are not allowed; use Decimal")
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def check_entry(kind: str, amount: Decimal) -> Decimal:
    """The rounded amount, or `InvalidEntry`: known kind, sign right for the kind, never zero."""
    if kind not in KINDS:
        raise InvalidEntry(f"unknown ledger kind {kind!r}")
    rounded = money(amount)
    if rounded == 0:
        raise InvalidEntry("a ledger entry cannot be zero")
    if kind in CREDIT_KINDS and rounded < 0:
        raise InvalidEntry(f"{kind} is a credit to the vendor: the amount must be positive")
    if kind in DEBIT_KINDS and rounded > 0:
        raise InvalidEntry(f"{kind} is a debit from the vendor: the amount must be negative")
    return rounded


def check_currency(currency: str) -> None:
    if currency != CURRENCY:
        raise InvalidEntry(f"unsupported currency {currency!r}: the ledger is in {CURRENCY}")


def balance(amounts: Iterable[Decimal]) -> Decimal:
    """What the vendor is owed: the sum of its entries. Negative means the vendor owes the
    platform; it is never refused (spec 04: set off against transfers, or a SEPA debit)."""
    return money(sum(amounts, Decimal("0")))


@dataclass(frozen=True)
class Statement:
    opening: Decimal
    credits: Decimal
    debits: Decimal
    closing: Decimal
    by_kind: Mapping[str, Decimal]


def build_statement(opening: Decimal, by_kind: Mapping[str, Decimal]) -> Statement:
    """A period: the opening balance, the credits and debits (adjustments count by sign)."""
    credits = balance(v for v in by_kind.values() if v > 0)
    debits = balance(v for v in by_kind.values() if v < 0)
    return Statement(
        opening=money(opening),
        credits=credits,
        debits=debits,
        closing=money(opening + credits + debits),
        by_kind=dict(by_kind),
    )


# --- Vendor money pages (VEN.9): periods, payout kinds, the extract reference and CSV safety

# What was sent to the vendor: the payouts page lists these kinds.
PAYOUT_KINDS = (PAYOUT, TRANSFER)
# Spec 11 §11.2: every ledger extract is a non-fiscal document with a stable reference ID.
NON_FISCAL_LABEL = "Documento non fiscale / Non-fiscal document"
# Why a period filter was ignored; the adapter turns the code into a translated message.
PERIOD_BAD_DATE = "bad_date"
PERIOD_REVERSED = "reversed"
PERIOD_INCOMPLETE = "incomplete"
CSV_APOSTROPHE = "'"
_FORMULA_SIGNS = ("=", "+", "-", "@")
_CONTROL_STARTS = ("\t", "\r", "\n", CSV_APOSTROPHE)


@dataclass(frozen=True)
class Period:
    """An inclusive date range (either side may be open) and why the input was ignored, if so."""

    first: dt.date | None
    last: dt.date | None
    error: str = ""


def parse_date(text: str) -> dt.date | None:
    """A `YYYY-MM-DD` date, or None for blank or invalid input."""
    try:
        return dt.date.fromisoformat(text.strip()) if text.strip() else None
    except ValueError:
        return None


def month_bounds(today: dt.date) -> tuple[dt.date, dt.date]:
    """First and last day of the month of `today`."""
    first = today.replace(day=1)
    following = (first + dt.timedelta(days=32)).replace(day=1)
    return first, following - dt.timedelta(days=1)


def read_period(
    first_text: str,
    last_text: str,
    *,
    today: dt.date,
    default_month: bool = False,
    closed: bool = False,
) -> Period:
    """The period typed in a filter. An invalid date or a start after the end is ignored, with
    its reason in `error`. With `default_month` an empty (or ignored) period is the month of
    `today`; with `closed` a period with one side only is that month too (a statement needs both
    ends)."""
    first, last = parse_date(first_text), parse_date(last_text)
    error = ""
    if (first_text.strip() and first is None) or (last_text.strip() and last is None):
        first = last = None
        error = PERIOD_BAD_DATE
    elif first and last and first > last:
        first = last = None
        error = PERIOD_REVERSED
    elif closed and (first is None) != (last is None):
        first = last = None
        error = PERIOD_INCOMPLETE
    if default_month and first is None and last is None:
        first, last = month_bounds(today)
    return Period(first, last, error)


def period_bounds(
    first: dt.date | None, last: dt.date | None, tz: dt.tzinfo
) -> tuple[dt.datetime | None, dt.datetime | None]:
    """Instants for an inclusive date range in `tz`: `start` inclusive, `end` exclusive (the day
    after `last`). A missing side stays open."""
    start = dt.datetime.combine(first, dt.time.min, tzinfo=tz) if first else None
    end = dt.datetime.combine(last + dt.timedelta(days=1), dt.time.min, tzinfo=tz) if last else None
    return start, end


def extract_reference(vendor_public_id: str, first: dt.date | None, last: dt.date | None) -> str:
    """The stable reference of a ledger extract: the same vendor and period give the same ID, so
    an outside invoice or a support request can cite it (spec 04, spec 11 §11.2)."""
    vendor = vendor_public_id.replace("-", "")[:8].upper()
    start = first.strftime("%Y%m%d") if first else "START"
    end = last.strftime("%Y%m%d") if last else "END"
    return f"LX-{vendor}-{start}-{end}"


def csv_safe(cell: str) -> str:
    """A text cell a spreadsheet cannot read as a formula (SEC.5, OWASP "CSV injection"): one that
    starts with a tab, CR, LF or apostrophe, or (after whitespace, in NFKC form) with `=`, `+`,
    `-` or `@`, gets a leading apostrophe. Amounts are written by `signed`, never through here."""
    if not cell:
        return cell
    if cell.startswith(_CONTROL_STARTS):
        return CSV_APOSTROPHE + cell
    text = cell.lstrip()
    if text and unicodedata.normalize("NFKC", text[0])[:1] in _FORMULA_SIGNS:
        return CSV_APOSTROPHE + cell
    return cell


def signed(amount: Decimal) -> str:
    """`+12.50` / `-3.00`: an amount rounded to the cent with its sign, for the CSV."""
    return f"{money(amount):+.2f}"
