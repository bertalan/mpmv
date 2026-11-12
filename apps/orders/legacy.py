"""Pure reading of a legacy order record for the tax archive (MIG.4, MIG.20). No ORM.

The input is a plain dict, as the MIG.1 extractor yields it. This module keeps only what a tax
audit needs and drops everything else on the floor: the shipping address, phone, e-mail, IP
address, user agent, geolocation, attribution and order notes are never read, so a canary value
in any of them cannot reach the output (a whitelist, not a blacklist).

Input shape (every money value is a string with at most two decimals, a rate at most three):

    {
      "legacy_order_id": 4711,
      "status": "completed",            # completed | refunded | cancelled | failed
      "created_at": "2019-05-03T10:00:00+00:00",   # timezone-aware ISO 8601
      "paid_at": "2019-05-03T10:05:00+00:00" | None,
      "billing": {"name", "company", "address_1", "address_2", "city", "postcode", "state",
                  "country", "vat_id"},
      "shipping_country": "IT",         # the country only, never the address
      "tax_total": "4.40",              # the sum of the legacy tax lines
      "vendors": [{
          "vendor_key": "A",
          "lines": [{"sku", "title", "variant_label", "quantity", "unit_price_net",
                     "discount_net", "tax_rate", "tax_reason"}],
          "shipping_net": "5.00", "shipping_tax_rate": "22", "shipping_tax_reason": "",
          "commission": {...}           # the per-order snapshot, as recorded
      }],
      "refunds": [{"amount": "5.00", "date": "2019-06-01", "reason": "damaged"}]
    }

A record with a problem is refused whole, with one message per problem (a report, not a
half-loaded order). An order still in progress (processing, on hold, pending) is refused until
it is closed on the legacy site.
"""

import datetime as dt
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from . import engine

_MONEY_RE = re.compile(r"^\d{1,10}(\.\d{1,2})?$")
_RATE_RE = re.compile(r"^\d{1,3}(\.\d{1,3})?$")
_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")

IN_PROGRESS = frozenset({"processing", "on-hold", "pending"})
CANCELLED = frozenset({"cancelled", "failed"})
KNOWN = frozenset({"completed", "refunded"}) | CANCELLED
BILLING_KEYS = (
    "name",
    "company",
    "address_1",
    "address_2",
    "city",
    "postcode",
    "state",
    "country",
    "vat_id",
)
ADDRESS_KEYS = (("line1", "address_1"), ("line2", "address_2"), ("city", "city"))
ZERO = Decimal("0.00")


class LegacyRecordError(ValueError):
    """The record cannot be loaded; `problems` is the report, one message per problem."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


class OrderInProgress(LegacyRecordError):
    """The order is paid or open but not closed on the legacy site: loaded only when closed."""


@dataclass(frozen=True)
class ParsedLine:
    sku: str
    title: str
    variant_label: str
    quantity: int
    unit_price_net: Decimal
    discount_net: Decimal
    tax_rate: Decimal
    tax_reason: str


@dataclass(frozen=True)
class ParsedVendor:
    vendor_key: str
    lines: tuple[ParsedLine, ...]
    shipping_net: Decimal
    shipping_tax_rate: Decimal
    shipping_tax_reason: str
    commission: dict[str, Any]


@dataclass(frozen=True)
class ParsedOrder:
    legacy_order_id: int
    created_at: dt.datetime
    paid_at: dt.datetime | None
    customer_name: str
    vat_number: str
    billing_address: dict[str, str]
    billing_country: str
    shipping_country: str
    vendors: tuple[ParsedVendor, ...]
    refunds: tuple[dict[str, str], ...]
    order_state: str
    vendor_state: str
    refunded_total: Decimal
    problems: list[str] = field(default_factory=list)


def parse_money(value: Any, label: str, problems: list[str]) -> Decimal:
    """A money string as `Decimal`; anything else (float, comma, sign, junk) is a problem."""
    if isinstance(value, str) and _MONEY_RE.match(value.strip()):
        return Decimal(value.strip())
    problems.append(f"{label}: {value!r} is not a money amount")
    return ZERO


def parse_rate(value: Any, label: str, problems: list[str]) -> Decimal:
    if isinstance(value, str) and _RATE_RE.match(value.strip()):
        return Decimal(value.strip())
    problems.append(f"{label}: {value!r} is not a tax rate")
    return ZERO


def _aware(value: Any, label: str, problems: list[str]) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(str(value))
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None:
        problems.append(f"{label}: {value!r} is not a timezone-aware date and time")
        return dt.datetime(1970, 1, 1, tzinfo=dt.UTC)
    return parsed


def _text(source: Mapping[str, Any], key: str, limit: int) -> str:
    value = source.get(key)
    return str(value).strip()[:limit] if isinstance(value, str) else ""


def _parse_line(raw: Mapping[str, Any], where: str, problems: list[str]) -> ParsedLine:
    quantity = raw.get("quantity")
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1:
        problems.append(f"{where}: quantity {quantity!r} is not a positive whole number")
        quantity = 1
    unit = parse_money(raw.get("unit_price_net"), f"{where} unit price", problems)
    if unit <= 0:
        problems.append(f"{where}: a line with a zero price cannot be loaded")
    return ParsedLine(
        sku=_text(raw, "sku", 64),
        title=_text(raw, "title", 300),
        variant_label=_text(raw, "variant_label", 300),
        quantity=quantity,
        unit_price_net=unit,
        discount_net=parse_money(raw.get("discount_net", "0.00"), f"{where} discount", problems),
        tax_rate=parse_rate(raw.get("tax_rate"), f"{where} tax rate", problems),
        tax_reason=_text(raw, "tax_reason", 64),
    )


def _parse_vendor(raw: Mapping[str, Any], index: int, problems: list[str]) -> ParsedVendor:
    where = f"vendor {index}"
    lines = [
        _parse_line(line, f"{where} line {i}", problems)
        for i, line in enumerate(raw.get("lines") or [], start=1)
    ]
    if not lines:
        problems.append(f"{where}: no lines")
    commission = raw.get("commission")
    if not isinstance(commission, Mapping):
        problems.append(f"{where}: the commission snapshot is missing (it is never recomputed)")
        commission = {}
    return ParsedVendor(
        vendor_key=_text(raw, "vendor_key", 100),
        lines=tuple(lines),
        shipping_net=parse_money(raw.get("shipping_net", "0.00"), f"{where} shipping", problems),
        shipping_tax_rate=parse_rate(
            raw.get("shipping_tax_rate", "0"), f"{where} shipping tax rate", problems
        ),
        shipping_tax_reason=_text(raw, "shipping_tax_reason", 64),
        commission=dict(commission),
    )


def _parse_refunds(raw: Any, problems: list[str]) -> list[dict[str, str]]:
    refunds = []
    for i, item in enumerate(raw or [], start=1):
        where = f"refund {i}"
        if not isinstance(item, Mapping):
            problems.append(f"{where}: not a record")
            continue
        amount = parse_money(item.get("amount"), f"{where} amount", problems)
        if amount <= 0:
            problems.append(f"{where}: the amount must be above zero")
        try:
            date = dt.date.fromisoformat(str(item.get("date"))[:10])
        except ValueError:
            problems.append(f"{where}: {item.get('date')!r} is not a date")
            continue
        reason = _text(item, "reason", 500)
        refunds.append({"amount": str(amount), "date": date.isoformat(), "reason": reason})
    return refunds


def vendor_totals(vendor: ParsedVendor) -> engine.Totals:
    amounts = [
        engine.line_amounts(line.unit_price_net, line.quantity, line.discount_net, line.tax_rate)
        for line in vendor.lines
    ]
    return engine.totals(
        amounts,
        shipping_net=vendor.shipping_net,
        shipping_tax=engine.shipping_tax(vendor.shipping_net, vendor.shipping_tax_rate),
    )


def final_states(status: str, grand_total: Decimal, refunded: Decimal) -> tuple[str, str]:
    """The order state and the vendor-order state a legacy order is loaded in. A legacy order is
    history: it is loaded in its final state, with no transition."""
    if status in CANCELLED:
        return "cancelled", "cancelled"
    if (refunded >= grand_total and refunded > 0) or status == "refunded":
        return "refunded", "refunded"
    if refunded > 0:
        return "partially_refunded", "completed"
    return "closed", "completed"


def parse_record(record: Mapping[str, Any]) -> ParsedOrder:
    """Validate and reduce a legacy record to the tax-relevant fields; raises
    `LegacyRecordError` (all problems at once) or `OrderInProgress`."""
    problems: list[str] = []
    status = str(record.get("status", ""))
    if status in IN_PROGRESS:
        raise OrderInProgress(
            [f"the order is {status!r} on the legacy site: close or ship it there first"]
        )
    if status not in KNOWN:
        problems.append(f"status {status!r} is not one the archive loads")
    legacy_id = record.get("legacy_order_id")
    if not isinstance(legacy_id, int) or isinstance(legacy_id, bool) or legacy_id < 1:
        problems.append(f"legacy_order_id {legacy_id!r} is not a positive whole number")
        legacy_id = 0
    created = _aware(record.get("created_at"), "created_at", problems)
    paid_raw = record.get("paid_at")
    paid = _aware(paid_raw, "paid_at", problems) if paid_raw else None

    raw_billing = record.get("billing")
    billing = raw_billing if isinstance(raw_billing, Mapping) else {}
    if not billing:
        problems.append("billing: missing")
    country = _text(billing, "country", 2).upper()
    if not _COUNTRY_RE.match(country):
        problems.append(f"billing country {country!r} is not a two-letter code")
    shipping_country = _text(record, "shipping_country", 2).upper() or country
    if not _COUNTRY_RE.match(shipping_country):
        problems.append(f"shipping country {shipping_country!r} is not a two-letter code")
    address = {key: _text(billing, source, 200) for key, source in ADDRESS_KEYS}
    address["postcode"] = _text(billing, "postcode", 20)
    address["region"] = _text(billing, "state", 100)
    address["country"] = country
    name = _text(billing, "company", 200) or _text(billing, "name", 200)

    vendors = [
        _parse_vendor(raw, i, problems)
        for i, raw in enumerate(record.get("vendors") or [], start=1)
        if isinstance(raw, Mapping)
    ]
    if not vendors:
        problems.append("the order has no vendor part")
    legacy_tax = parse_money(record.get("tax_total"), "tax_total", problems)
    refunds = _parse_refunds(record.get("refunds"), problems)

    grand = ZERO
    tax = ZERO
    if not problems:
        totals = [vendor_totals(v) for v in vendors]
        tax = sum((t.tax_total for t in totals), ZERO)
        grand = sum((t.grand_total for t in totals), ZERO)
        if tax != legacy_tax:
            problems.append(f"the tax lines add up to {legacy_tax} but the lines give {tax}")
    refunded = sum((Decimal(r["amount"]) for r in refunds), ZERO)
    if not problems and refunded > grand:
        problems.append(f"refunds ({refunded}) exceed the order total ({grand})")
    if problems:
        raise LegacyRecordError(problems)
    order_state, vendor_state = final_states(status, grand, refunded)
    return ParsedOrder(
        legacy_order_id=legacy_id,
        created_at=created,
        paid_at=paid,
        customer_name=name,
        vat_number=_text(billing, "vat_id", 32),
        billing_address=address,
        billing_country=country,
        shipping_country=shipping_country,
        vendors=tuple(vendors),
        refunds=tuple(refunds),
        order_state=order_state,
        vendor_state=vendor_state,
        refunded_total=refunded,
    )
