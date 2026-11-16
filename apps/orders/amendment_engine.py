"""Pure rules of order amendments (ORD.20, owner decision D-47; spec 07 §Order amendments).

No ORM, no I/O (ARC.18: the import contract holds this module to the engine rules). The
services in `apps.orders.amendments` read the vendor order, build the context and store what
these functions return.

A vendor order is a chain of immutable versions: v1 is the placed order, every applied amendment
writes the next one. `recompute_vendor_order` replays the checkout on the current version with the
typed changes and returns the next version and the money delta; `classify_amendment` decides
whether the change is minor (applied at once) or substantial (proposed to the customer). The actor
never picks the kind.

VAT timing (joint tax and legal opinion of 2026-11-16, point 1.a). Each version line holds its tax
as **portions**, each with its own net, rate, rules and `tax_on` date:

- an unchanged line, or the unchanged part of a changed line, copies its portions: the amount
  already paid is never re-rated, even if a rate changed by law in the meantime;
- a reduction or a removed line is reversed at the rate of the portion it reduces (the newest
  portion first, so an earlier increment is reduced before the paid base);
- an increase or a new line adds a portion for the increment, decided on the amendment's tax date;
- a move to another country or tax territory reverses every line and taxes it again for the new
  destination on the amendment's tax date (a change of regime, art. 26 c. 2).

Shipping follows the goods (TAX.17): `resolve_shipping_tax` splits it over the goods' decisions,
so removing a line can move the shipping VAT split. Each line counts in the split under its base
decision (its first portion), so an increment taxed at a newer rate does not re-split the paid
shipping. A shipping portion whose decision was already in the previous version keeps that
version's `tax_on`; a new one takes the amendment's tax date.

Money: `Decimal`, `ROUND_HALF_UP` per line and per portion, never floats.
"""

import datetime as dt
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any, ClassVar, Final

from apps.core.countries import InvalidCountry, normalize_country
from apps.core.money import round_line
from apps.tax.engine import (
    ShippingCharge,
    TaxContext,
    TaxDecision,
    TerritoryRule,
    find_territory,
    is_northern_ireland,
    resolve_goods_tax,
    resolve_shipping_tax,
    tax_amount,
)
from apps.vendors.commission import compute_commission

ZERO: Final = Decimal("0.00")

MINOR: Final = "minor"
SUBSTANTIAL: Final = "substantial"
KINDS: Final = (MINOR, SUBSTANTIAL)

VENDOR_OWNER: Final = "vendor_owner"
VENDOR_MEMBER: Final = "vendor_member"
PLATFORM_OWNER: Final = "platform_owner"
SUPPORT: Final = "support"
INITIATORS: Final = (VENDOR_OWNER, VENDOR_MEMBER, PLATFORM_OWNER, SUPPORT)
STAFF_INITIATORS: Final = frozenset({PLATFORM_OWNER, SUPPORT})

# Operator setting `amendment_comparable_delay_days` (D-47): the default until the Manager
# setting exists (ORD.20 part 3).
DEFAULT_COMPARABLE_DELAY_DAYS: Final = 2

# An amendment is possible only before shipping (spec 07: "When").
AMENDABLE_VENDOR_ORDER_STATES: Final = frozenset({"pending", "accepted"})


class AmendmentRefused(ValueError):
    """The amendment cannot be drafted or applied. `str()` is a plain sentence for the actor."""


class AmendmentBlocked(AmendmentRefused):
    """The new destination fails the checkout's eligibility checks (territory change)."""


# --- versions --------------------------------------------------------------------------------


def _dec(value: Any) -> Decimal:
    if isinstance(value, float):
        raise TypeError("float amounts are not allowed")
    return Decimal(str(value))


def _opt(value: Any) -> str | None:
    return None if value in (None, "") else str(value)


@dataclass(frozen=True)
class VersionPortion:
    """A part of a line (or of the shipping) taxed under one decision on one date."""

    net: Decimal
    tax: Decimal
    rate: Decimal
    reason: str
    tax_on: dt.date
    country_of_taxation: str | None = None
    legal_note_key: str | None = None
    nature_code: str | None = None

    @classmethod
    def taxed(cls, net: Decimal, decision: TaxDecision, tax_on: dt.date) -> "VersionPortion":
        return cls(
            net=round_line(net),
            tax=tax_amount(round_line(net), decision.rate),
            rate=decision.rate,
            reason=str(decision.reason),
            tax_on=tax_on,
            country_of_taxation=decision.country_of_taxation,
            legal_note_key=decision.legal_note_key,
            nature_code=decision.nature_code,
        )

    @property
    def decision(self) -> TaxDecision:
        return TaxDecision(
            rate=self.rate,
            reason=self.reason,  # type: ignore[arg-type]
            country_of_taxation=self.country_of_taxation,
            legal_note_key=self.legal_note_key,
            nature_code=self.nature_code,
        )

    def resized(self, net: Decimal) -> "VersionPortion":
        """The same portion (rate, rules, date) on a smaller net: the tax at the original rate."""
        return replace(self, net=net, tax=tax_amount(net, self.rate))

    def to_json(self) -> dict[str, Any]:
        return {
            "net": str(self.net),
            "tax": str(self.tax),
            "rate": str(self.rate),
            "reason": self.reason,
            "tax_on": self.tax_on.isoformat(),
            "country_of_taxation": self.country_of_taxation,
            "legal_note_key": self.legal_note_key,
            "nature_code": self.nature_code,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "VersionPortion":
        return cls(
            net=_dec(data["net"]),
            tax=_dec(data["tax"]),
            rate=_dec(data["rate"]),
            reason=str(data["reason"]),
            tax_on=dt.date.fromisoformat(str(data["tax_on"])),
            country_of_taxation=_opt(data.get("country_of_taxation")),
            legal_note_key=_opt(data.get("legal_note_key")),
            nature_code=_opt(data.get("nature_code")),
        )


def _sum(values: Iterable[Decimal]) -> Decimal:
    return sum(values, start=ZERO)


@dataclass(frozen=True)
class VersionLine:
    """One line of a version. `line_key` is stable across versions: the line it replaces keeps
    it; a substitute gets its own key and names the replaced one in `replaces`."""

    line_key: str
    sku: str
    title: str
    unit_price_net: Decimal
    quantity: int
    discount_net: Decimal
    portions: tuple[VersionPortion, ...]
    product_id: int | None = None
    variant_label: str = ""
    replaces: str = ""

    @property
    def net(self) -> Decimal:
        return _sum(p.net for p in self.portions)

    @property
    def tax(self) -> Decimal:
        return _sum(p.tax for p in self.portions)

    @property
    def gross(self) -> Decimal:
        return self.net + self.tax

    def to_json(self) -> dict[str, Any]:
        return {
            "line_key": self.line_key,
            "sku": self.sku,
            "title": self.title,
            "unit_price_net": str(self.unit_price_net),
            "quantity": self.quantity,
            "discount_net": str(self.discount_net),
            "portions": [p.to_json() for p in self.portions],
            "product_id": self.product_id,
            "variant_label": self.variant_label,
            "replaces": self.replaces,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "VersionLine":
        return cls(
            line_key=str(data["line_key"]),
            sku=str(data["sku"]),
            title=str(data["title"]),
            unit_price_net=_dec(data["unit_price_net"]),
            quantity=int(data["quantity"]),
            discount_net=_dec(data["discount_net"]),
            portions=tuple(VersionPortion.from_json(p) for p in data["portions"]),
            product_id=data.get("product_id"),
            variant_label=str(data.get("variant_label") or ""),
            replaces=str(data.get("replaces") or ""),
        )


@dataclass(frozen=True)
class VersionShipping:
    """The package's shipping in a version: the method and what the customer pays for it."""

    net: Decimal
    portions: tuple[VersionPortion, ...] = ()
    key: str = ""
    label: str = ""
    carrier_code: str = ""
    zone_code: str = ""
    min_days: int | None = None
    max_days: int | None = None

    @property
    def tax(self) -> Decimal:
        return _sum(p.tax for p in self.portions)

    def to_json(self) -> dict[str, Any]:
        return {
            "net": str(self.net),
            "portions": [p.to_json() for p in self.portions],
            "key": self.key,
            "label": self.label,
            "carrier_code": self.carrier_code,
            "zone_code": self.zone_code,
            "min_days": self.min_days,
            "max_days": self.max_days,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "VersionShipping":
        return cls(
            net=_dec(data["net"]),
            portions=tuple(VersionPortion.from_json(p) for p in data.get("portions", ())),
            key=str(data.get("key") or ""),
            label=str(data.get("label") or ""),
            carrier_code=str(data.get("carrier_code") or ""),
            zone_code=str(data.get("zone_code") or ""),
            min_days=data.get("min_days"),
            max_days=data.get("max_days"),
        )


@dataclass(frozen=True)
class VersionTotals:
    goods_net: Decimal
    shipping_net: Decimal
    tax_total: Decimal
    gross_total: Decimal


@dataclass(frozen=True)
class VersionSnapshot:
    """The full content of one version of a vendor order (no personal data: the address is kept
    beside the snapshot, where the retention scrub can reach it).

    `commission` is the rule frozen at payment (v1); an amendment never re-reads a live rule.
    `destination_country` is the delivery country, a fiscal fact."""

    lines: tuple[VersionLine, ...]
    shipping: VersionShipping
    commission: Mapping[str, Any]
    destination_country: str

    @property
    def totals(self) -> VersionTotals:
        goods = _sum(line.net for line in self.lines)
        tax = _sum(line.tax for line in self.lines) + self.shipping.tax
        return VersionTotals(
            goods_net=goods,
            shipping_net=self.shipping.net,
            tax_total=tax,
            gross_total=goods + self.shipping.net + tax,
        )

    def line(self, key: str) -> VersionLine | None:
        return next((line for line in self.lines if line.line_key == key), None)

    def to_json(self) -> dict[str, Any]:
        return {
            "lines": [line.to_json() for line in self.lines],
            "shipping": self.shipping.to_json(),
            "commission": dict(self.commission),
            "destination_country": self.destination_country,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "VersionSnapshot":
        return cls(
            lines=tuple(VersionLine.from_json(line) for line in data["lines"]),
            shipping=VersionShipping.from_json(data["shipping"]),
            commission=dict(data.get("commission") or {}),
            destination_country=str(data.get("destination_country") or ""),
        )


# --- typed changes ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NewLine:
    """A line that is not in the current version (an added line or a substitute)."""

    line_key: str
    sku: str
    title: str
    unit_price_net: Decimal
    quantity: int
    fiscal_category: str
    product_id: int | None = None
    variant_label: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "line_key": self.line_key,
            "sku": self.sku,
            "title": self.title,
            "unit_price_net": str(self.unit_price_net),
            "quantity": self.quantity,
            "fiscal_category": self.fiscal_category,
            "product_id": self.product_id,
            "variant_label": self.variant_label,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "NewLine":
        return cls(
            line_key=str(data["line_key"]),
            sku=str(data["sku"]),
            title=str(data["title"]),
            unit_price_net=_dec(data["unit_price_net"]),
            quantity=int(data["quantity"]),
            fiscal_category=str(data["fiscal_category"]),
            product_id=data.get("product_id"),
            variant_label=str(data.get("variant_label") or ""),
        )


@dataclass(frozen=True)
class ShippingOffer:
    """A shipping method of the same vendor quoted for the package (net of VAT)."""

    key: str
    label: str
    net: Decimal
    carrier_code: str = ""
    zone_code: str = ""
    min_days: int | None = None
    max_days: int | None = None


@dataclass(frozen=True)
class RemoveLine:
    kind: ClassVar[str] = "remove_line"
    line_key: str


@dataclass(frozen=True)
class ChangeQuantity:
    kind: ClassVar[str] = "quantity"
    line_key: str
    quantity: int


@dataclass(frozen=True)
class ChangePrice:
    kind: ClassVar[str] = "price"
    line_key: str
    unit_price_net: Decimal


@dataclass(frozen=True)
class AddLine:
    kind: ClassVar[str] = "add_line"
    line: NewLine


@dataclass(frozen=True)
class Substitute:
    kind: ClassVar[str] = "substitute"
    line_key: str
    line: NewLine


@dataclass(frozen=True)
class ChangeCarrier:
    kind: ClassVar[str] = "carrier"
    offer: ShippingOffer


@dataclass(frozen=True)
class ChangeAddress:
    """A new delivery address. `shipping` is the package's method quoted again for the new
    address (the service fills it); `requested_by_customer` says who asked."""

    kind: ClassVar[str] = "address"
    address: Mapping[str, Any]
    requested_by_customer: bool
    shipping: ShippingOffer | None = None

    @property
    def country(self) -> str:
        return str(self.address.get("country") or "")

    @property
    def postcode(self) -> str:
        return str(self.address.get("postcode") or "")


@dataclass(frozen=True)
class ChangeDeliveryTime:
    kind: ClassVar[str] = "delivery_time"
    max_days: int
    min_days: int | None = None


type AmendmentChange = (
    RemoveLine
    | ChangeQuantity
    | ChangePrice
    | AddLine
    | Substitute
    | ChangeCarrier
    | ChangeAddress
    | ChangeDeliveryTime
)


def _offer_json(offer: ShippingOffer) -> dict[str, Any]:
    return {
        "key": offer.key,
        "label": offer.label,
        "net": str(offer.net),
        "carrier_code": offer.carrier_code,
        "zone_code": offer.zone_code,
        "min_days": offer.min_days,
        "max_days": offer.max_days,
    }


def _offer_from(data: Mapping[str, Any]) -> ShippingOffer:
    return ShippingOffer(
        key=str(data["key"]),
        label=str(data.get("label") or ""),
        net=_dec(data["net"]),
        carrier_code=str(data.get("carrier_code") or ""),
        zone_code=str(data.get("zone_code") or ""),
        min_days=data.get("min_days"),
        max_days=data.get("max_days"),
    )


def change_to_json(change: AmendmentChange, *, with_address: bool = True) -> dict[str, Any]:
    """The change as stored on the amendment. `with_address=False` leaves the address out (the
    audit trail keeps no personal data)."""
    data: dict[str, Any] = {"kind": change.kind}
    match change:
        case RemoveLine(line_key=key):
            data["line_key"] = key
        case ChangeQuantity(line_key=key, quantity=quantity):
            data.update(line_key=key, quantity=quantity)
        case ChangePrice(line_key=key, unit_price_net=price):
            data.update(line_key=key, unit_price_net=str(price))
        case AddLine(line=line):
            data["line"] = line.to_json()
        case Substitute(line_key=key, line=line):
            data.update(line_key=key, line=line.to_json())
        case ChangeCarrier(offer=offer):
            data["offer"] = _offer_json(offer)
        case ChangeAddress():
            data["country"] = change.country
            data["requested_by_customer"] = change.requested_by_customer
            if with_address:
                data["address"] = dict(change.address)
            if change.shipping is not None:
                data["shipping"] = _offer_json(change.shipping)
        case ChangeDeliveryTime(max_days=max_days, min_days=min_days):
            data.update(max_days=max_days, min_days=min_days)
    return data


def change_from_json(data: Mapping[str, Any]) -> AmendmentChange:
    kind = data.get("kind")
    if kind == RemoveLine.kind:
        return RemoveLine(str(data["line_key"]))
    if kind == ChangeQuantity.kind:
        return ChangeQuantity(str(data["line_key"]), int(data["quantity"]))
    if kind == ChangePrice.kind:
        return ChangePrice(str(data["line_key"]), _dec(data["unit_price_net"]))
    if kind == AddLine.kind:
        return AddLine(NewLine.from_json(data["line"]))
    if kind == Substitute.kind:
        return Substitute(str(data["line_key"]), NewLine.from_json(data["line"]))
    if kind == ChangeCarrier.kind:
        return ChangeCarrier(_offer_from(data["offer"]))
    if kind == ChangeAddress.kind:
        shipping = data.get("shipping")
        return ChangeAddress(
            address=dict(data.get("address") or {"country": data.get("country", "")}),
            requested_by_customer=bool(data["requested_by_customer"]),
            shipping=_offer_from(shipping) if shipping else None,
        )
    if kind == ChangeDeliveryTime.kind:
        return ChangeDeliveryTime(int(data["max_days"]), data.get("min_days"))
    raise ValueError(f"{kind!r} is not a kind of amendment change")


# --- territory -------------------------------------------------------------------------------


def tax_territory(country: str, postcode: str, territories: Iterable[TerritoryRule]) -> str:
    """The tax territory of an address: `XI` for Northern Ireland, the TAX.1 territory code
    (Livigno, the Canary Islands...) when the postcode lies in one, otherwise the country."""
    if is_northern_ireland(country, postcode):
        return "XI"
    try:
        code = normalize_country(country)
    except InvalidCountry:
        return ""
    rule = find_territory(code, postcode, territories)
    return rule.code if rule is not None else code


# --- recompute -------------------------------------------------------------------------------


@dataclass(frozen=True)
class RecomputeContext:
    """What the recompute needs besides the version and the changes.

    `tax_on` is the amendment's tax date (the date it is applied, or of the second payment).
    `tax` is the tax context on that date for the destination after the change: it decides the
    portions of increments, new lines and a territory change, and the TAX.17 shipping split.
    `address_before` holds the country and postcode of the current delivery address.
    `categories` maps a line key to its product's fiscal category (needed to tax a line again)."""

    tax_on: dt.date
    tax: TaxContext
    address_before: Mapping[str, Any]
    categories: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class TaxMovement:
    """A change of VAT between two versions under one decision and date: negative when
    reversed (a reduction, a removed line), positive when added."""

    part: str  # a line key, or "shipping"
    net: Decimal
    tax: Decimal
    rate: Decimal
    tax_on: dt.date

    def to_json(self) -> dict[str, Any]:
        return {
            "part": self.part,
            "net": str(self.net),
            "tax": str(self.tax),
            "rate": str(self.rate),
            "tax_on": self.tax_on.isoformat(),
        }


@dataclass(frozen=True)
class AmendmentPreview:
    """The outcome of `recompute_vendor_order`: the next version and what moves.

    `money_delta` is the gross after minus the gross before: negative is a refund to the customer,
    positive is a supplement to collect. `vendor_absorbs` is the extra shipping cost of a dearer
    carrier that the vendor takes on (the customer's shipping stays the same)."""

    before: VersionSnapshot
    after: VersionSnapshot
    commission_before: Decimal
    commission_after: Decimal
    territory_changed: bool
    vendor_absorbs: Decimal
    tax_movements: tuple[TaxMovement, ...]

    @property
    def money_delta(self) -> Decimal:
        return self.after.totals.gross_total - self.before.totals.gross_total

    @property
    def refund(self) -> Decimal:
        return max(ZERO, -self.money_delta)

    @property
    def to_pay(self) -> Decimal:
        return max(ZERO, self.money_delta)

    @property
    def commission_delta(self) -> Decimal:
        return self.commission_after - self.commission_before

    @property
    def tax_reversed(self) -> Decimal:
        return -_sum(m.tax for m in self.tax_movements if m.tax < 0)

    @property
    def tax_added(self) -> Decimal:
        return _sum(m.tax for m in self.tax_movements if m.tax > 0)

    def to_json(self) -> dict[str, Any]:
        before, after = self.before.totals, self.after.totals
        return {
            "before": _totals_json(before),
            "after": _totals_json(after),
            "money_delta": str(self.money_delta),
            "refund": str(self.refund),
            "to_pay": str(self.to_pay),
            "commission_before": str(self.commission_before),
            "commission_after": str(self.commission_after),
            "commission_delta": str(self.commission_delta),
            "territory_changed": self.territory_changed,
            "vendor_absorbs": str(self.vendor_absorbs),
            "tax_movements": [m.to_json() for m in self.tax_movements],
            "lines": _line_diff(self.before, self.after),
        }


def _totals_json(totals: VersionTotals) -> dict[str, str]:
    return {
        "goods_net": str(totals.goods_net),
        "shipping_net": str(totals.shipping_net),
        "tax_total": str(totals.tax_total),
        "gross_total": str(totals.gross_total),
    }


def _line_summary(line: VersionLine | None) -> dict[str, Any] | None:
    if line is None:
        return None
    return {
        "sku": line.sku,
        "unit_price_net": str(line.unit_price_net),
        "quantity": line.quantity,
        "net": str(line.net),
        "tax": str(line.tax),
    }


def _line_diff(before: VersionSnapshot, after: VersionSnapshot) -> list[dict[str, Any]]:
    keys = [line.line_key for line in before.lines]
    keys += [line.line_key for line in after.lines if line.line_key not in keys]
    return [
        {
            "line_key": key,
            "before": _line_summary(before.line(key)),
            "after": _line_summary(after.line(key)),
        }
        for key in keys
        if before.line(key) != after.line(key)
    ]


def _commission(snapshot: VersionSnapshot) -> Decimal:
    totals = snapshot.totals
    rule = dict(snapshot.commission)
    rule.setdefault("promo_label", "")
    return compute_commission(
        rule,
        goods_net=totals.goods_net,
        gross_paid=totals.gross_total,
        shipping_net=totals.shipping_net,
    ).commission


def _line_net(unit_price_net: Decimal, quantity: int, discount_net: Decimal) -> Decimal:
    if quantity < 1:
        raise AmendmentRefused("A line needs at least one unit; remove the line instead.")
    if unit_price_net <= 0 or unit_price_net != round_line(unit_price_net):
        raise AmendmentRefused("The unit price must be an amount above zero, in cents.")
    return round_line(unit_price_net * quantity - discount_net)


def _reprice(
    line: VersionLine,
    *,
    unit_price_net: Decimal,
    quantity: int,
    discount_net: Decimal,
    increment: TaxDecision | None,
    tax_on: dt.date,
) -> VersionLine:
    """The line at its new price or quantity: a lower net reduces the newest portions at their
    own rate, a higher net adds a portion for the increment (decided on the amendment's date)."""
    new_net = _line_net(unit_price_net, quantity, discount_net)
    portions = list(line.portions)
    delta = new_net - line.net
    if delta < 0:
        remaining = -delta
        while remaining > 0 and portions:
            last = portions.pop()
            if last.net > remaining:
                portions.append(last.resized(last.net - remaining))
                remaining = ZERO
            else:
                remaining -= last.net
    elif delta > 0:
        if increment is None:
            raise AmendmentRefused(f"The tax of the increase of line {line.sku} is not known.")
        portions.append(VersionPortion.taxed(delta, increment, tax_on))
    return replace(
        line,
        unit_price_net=unit_price_net,
        quantity=quantity,
        discount_net=discount_net,
        portions=tuple(portions),
    )


def _decide(ctx: RecomputeContext, key: str, category: str | None) -> TaxDecision:
    category = category or ctx.categories.get(key)
    if not category:
        raise AmendmentRefused(
            "The product of this line is no longer in the catalogue, so its tax cannot be "
            "decided again; the line can only be reduced or removed."
        )
    return resolve_goods_tax(ctx.tax, category)


def _new_line(line: NewLine, ctx: RecomputeContext, *, replaces: str = "") -> VersionLine:
    net = _line_net(line.unit_price_net, line.quantity, ZERO)
    decision = _decide(ctx, line.line_key, line.fiscal_category)
    return VersionLine(
        line_key=line.line_key,
        sku=line.sku,
        title=line.title,
        unit_price_net=line.unit_price_net,
        quantity=line.quantity,
        discount_net=ZERO,
        portions=(VersionPortion.taxed(net, decision, ctx.tax_on),),
        product_id=line.product_id,
        variant_label=line.variant_label,
        replaces=replaces,
    )


def _shipping_portions(
    ctx: RecomputeContext,
    lines: Sequence[VersionLine],
    net: Decimal,
    previous: Sequence[VersionPortion],
) -> tuple[VersionPortion, ...]:
    """TAX.17: the shipping split over the goods, each line under its base decision."""
    if not lines or net == 0:
        return ()
    goods = [(line.net, line.portions[0].decision) for line in lines if line.portions]
    if not goods:
        return ()
    dates = {(p.rate, p.reason): p.tax_on for p in previous}
    return tuple(
        VersionPortion(
            net=portion.net,
            tax=portion.tax,
            rate=portion.rate,
            reason=str(portion.reason),
            tax_on=dates.get((portion.rate, str(portion.reason)), ctx.tax_on),
            country_of_taxation=portion.country_of_taxation,
            legal_note_key=portion.legal_note_key,
            nature_code=portion.nature_code,
        )
        for portion in resolve_shipping_tax(ctx.tax, goods, ShippingCharge(net=net))
    )


def _movements(
    part: str, before: Sequence[VersionPortion], after: Sequence[VersionPortion]
) -> list[TaxMovement]:
    """The VAT differences of one part, grouped by decision and date."""
    sums: dict[tuple[TaxDecision, dt.date], list[Decimal]] = {}
    for sign, portions in ((-1, before), (1, after)):
        for p in portions:
            entry = sums.setdefault((p.decision, p.tax_on), [ZERO, ZERO])
            entry[0] += sign * p.net
            entry[1] += sign * p.tax
    return [
        TaxMovement(part=part, net=net, tax=tax, rate=decision.rate, tax_on=on)
        for (decision, on), (net, tax) in sums.items()
        if net or tax
    ]


def recompute_vendor_order(
    version: VersionSnapshot, changes: Sequence[AmendmentChange], ctx: RecomputeContext
) -> AmendmentPreview:
    """Replay the checkout of a vendor order on `version` with `changes` (spec 07, "One engine").

    Goods tax per line with the VAT timing above, the shipping (a carrier change takes the new
    method's price, or keeps the customer's price when the new one is dearer: the vendor absorbs
    it), the shipping VAT split (TAX.17), the commission with the frozen rule and the money delta.
    The customer's tax status is never amended. Raises `AmendmentRefused` for a change that
    cannot be computed (an unknown line, a quantity below one, a price that is not in cents).
    """
    if not changes:
        raise AmendmentRefused("An amendment needs at least one change.")
    lines: dict[str, VersionLine] = {line.line_key: line for line in version.lines}
    order: list[str] = list(lines)
    shipping = version.shipping
    vendor_absorbs = ZERO
    country_before = str(ctx.address_before.get("country") or version.destination_country)
    territory_before = tax_territory(
        country_before, str(ctx.address_before.get("postcode") or ""), ctx.tax.territories
    )
    territory_after = territory_before
    destination_country = version.destination_country

    def existing(key: str) -> VersionLine:
        if key not in lines:
            raise AmendmentRefused(f"The order has no line {key!r} in its current version.")
        return lines[key]

    for change in changes:
        match change:
            case RemoveLine(line_key=key):
                existing(key)
                del lines[key]
                order.remove(key)
            case ChangeQuantity(line_key=key, quantity=quantity):
                line = existing(key)
                discount = line.discount_net
                if quantity < line.quantity:  # the discount goes with the units removed
                    discount = round_line(line.discount_net * quantity / line.quantity)
                lines[key] = _reprice(
                    line,
                    unit_price_net=line.unit_price_net,
                    quantity=quantity,
                    discount_net=discount,
                    increment=(_decide(ctx, key, None) if quantity > line.quantity else None),
                    tax_on=ctx.tax_on,
                )
            case ChangePrice(line_key=key, unit_price_net=price):
                line = existing(key)
                discount = min(line.discount_net, round_line(price * line.quantity))
                higher = price * line.quantity - discount > line.net
                lines[key] = _reprice(
                    line,
                    unit_price_net=price,
                    quantity=line.quantity,
                    discount_net=discount,
                    increment=_decide(ctx, key, None) if higher else None,
                    tax_on=ctx.tax_on,
                )
            case AddLine(line=new):
                if new.line_key in lines:
                    raise AmendmentRefused(f"The order already has a line {new.line_key!r}.")
                lines[new.line_key] = _new_line(new, ctx)
                order.append(new.line_key)
            case Substitute(line_key=key, line=new):
                existing(key)
                if new.line_key in lines:
                    raise AmendmentRefused(f"The order already has a line {new.line_key!r}.")
                lines[new.line_key] = _new_line(new, ctx, replaces=key)
                order[order.index(key)] = new.line_key
                del lines[key]
            case ChangeCarrier(offer=offer):
                charged = min(offer.net, shipping.net)
                vendor_absorbs = max(ZERO, offer.net - shipping.net)
                shipping = replace(
                    shipping,
                    net=charged,
                    key=offer.key,
                    label=offer.label,
                    carrier_code=offer.carrier_code,
                    zone_code=offer.zone_code,
                    min_days=offer.min_days,
                    max_days=offer.max_days,
                )
            case ChangeAddress():
                territory_after = tax_territory(
                    change.country, change.postcode, ctx.tax.territories
                )
                destination_country = normalize_country(change.country)
                if change.shipping is not None:
                    shipping = replace(
                        shipping,
                        net=change.shipping.net,
                        zone_code=change.shipping.zone_code,
                        min_days=change.shipping.min_days,
                        max_days=change.shipping.max_days,
                    )
            case ChangeDeliveryTime(max_days=max_days, min_days=min_days):
                shipping = replace(shipping, max_days=max_days, min_days=min_days)

    territory_changed = territory_after != territory_before
    if territory_changed:  # a change of regime: every line reversed and taxed again
        for key in order:
            line = lines[key]
            decision = _decide(ctx, key, None)
            lines[key] = replace(
                line, portions=(VersionPortion.taxed(line.net, decision, ctx.tax_on),)
            )
    if not order:
        raise AmendmentRefused("An amendment cannot remove every line: cancel the package instead.")
    new_lines = tuple(lines[key] for key in order)
    shipping = replace(
        shipping,
        portions=_shipping_portions(ctx, new_lines, shipping.net, version.shipping.portions),
    )
    after = VersionSnapshot(
        lines=new_lines,
        shipping=shipping,
        commission=version.commission,
        destination_country=destination_country,
    )
    movements: list[TaxMovement] = []
    for key in [*version_keys(version), *(k for k in order if version.line(k) is None)]:
        old, new_line = version.line(key), after.line(key)
        movements += _movements(
            key, old.portions if old else (), new_line.portions if new_line else ()
        )
    movements += _movements("shipping", version.shipping.portions, after.shipping.portions)
    return AmendmentPreview(
        before=version,
        after=after,
        commission_before=_commission(version),
        commission_after=_commission(after),
        territory_changed=territory_changed,
        vendor_absorbs=vendor_absorbs,
        tax_movements=tuple(movements),
    )


def version_keys(version: VersionSnapshot) -> list[str]:
    return [line.line_key for line in version.lines]


# --- classification --------------------------------------------------------------------------


@dataclass(frozen=True)
class Classification:
    """`kind` is minor or substantial; `reasons` say in plain words why it is substantial."""

    kind: str
    reasons: tuple[str, ...] = ()


def _days(value: int | None) -> str:
    return "unknown" if value is None else f"{value} days"


def _slower(before: int | None, after: int | None, allowed: int) -> str | None:
    """Why a new delivery time is not comparable, or None when it is."""
    if after is None:
        return (
            "the new delivery time is not known, so it cannot be shown to be comparable "
            "to the old one"
        )
    if before is None:
        return None
    extra = after - before
    if extra > allowed:
        return (
            f"the new maximum delivery time ({_days(after)}) is {extra} days longer than the old "
            f"one ({_days(before)}), more than the {allowed} days a minor change allows"
        )
    return None


def classify_amendment(
    preview: AmendmentPreview,
    changes: Sequence[AmendmentChange],
    initiator: str,
    *,
    comparable_delay_days: int = DEFAULT_COMPARABLE_DELAY_DAYS,
) -> Classification:
    """Minor or substantial (spec 07, "Minor changes" and "Substantial changes").

    Minor: a carrier at the same or a lower price (a dearer one is absorbed by the vendor) with a
    comparable delivery time; a lower price, a lower quantity or a removed line; an address
    correction asked by the customer in the same country and tax territory that changes neither
    the tax nor raises the shipping. Everything else is substantial, and so is any change whose
    recompute raises the gross. One substantial part makes the whole amendment substantial.
    """
    if initiator not in INITIATORS:
        raise ValueError(f"{initiator!r} is not an amendment initiator")
    before, after = preview.before, preview.after
    reasons: list[str] = []
    for change in changes:
        match change:
            case Substitute():
                reasons.append("a substitute product needs the customer's consent")
            case AddLine():
                reasons.append("an added line needs the customer's acceptance")
            case ChangeQuantity(line_key=key, quantity=quantity):
                old = before.line(key)
                if old is not None and quantity > old.quantity:
                    reasons.append("a higher quantity needs the customer's acceptance")
            case ChangePrice(line_key=key, unit_price_net=price):
                old = before.line(key)
                if old is not None and price > old.unit_price_net:
                    reasons.append("a higher price needs the customer's acceptance")
            case ChangeCarrier(offer=offer):
                slower = _slower(before.shipping.max_days, offer.max_days, comparable_delay_days)
                if slower:
                    reasons.append(slower)
            case ChangeDeliveryTime(max_days=max_days):
                slower = _slower(before.shipping.max_days, max_days, comparable_delay_days)
                if slower:
                    reasons.append(slower)
            case ChangeAddress():
                if not change.requested_by_customer:
                    reasons.append(
                        "an address change made on the vendor's or staff's own initiative needs "
                        "the customer's acceptance"
                    )
                if preview.territory_changed:
                    reasons.append(
                        "the new address is in another country or tax territory, which is never "
                        "a minor change"
                    )
                elif _tax_changed(before, after):
                    reasons.append("the new address changes the tax")
                if after.shipping.net > before.shipping.net:
                    reasons.append("the new address costs more to ship")
    if preview.money_delta > 0:
        reasons.append(
            f"the recompute raises what the customer pays by {preview.money_delta} EUR, and a "
            "minor change never does"
        )
    unique = tuple(dict.fromkeys(reasons))
    return Classification(SUBSTANTIAL if unique else MINOR, unique)


def _tax_changed(before: VersionSnapshot, after: VersionSnapshot) -> bool:
    rates = {line.line_key: [p.decision for p in line.portions] for line in before.lines}
    return any(
        [p.decision for p in line.portions] != rates.get(line.line_key, [])
        for line in after.lines
        if line.line_key in rates
    )


def staff_change_error(initiator: str, changes: Sequence[AmendmentChange]) -> str | None:
    """D-47 (4): platform staff only change the carrier, or the address on the customer's request;
    never products or prices (the vendor is the seller). None when allowed."""
    if initiator not in STAFF_INITIATORS:
        return None
    for change in changes:
        if isinstance(change, ChangeCarrier | ChangeDeliveryTime):
            continue
        if isinstance(change, ChangeAddress) and change.requested_by_customer:
            continue
        return (
            "Platform staff may only change the carrier, or the address when the customer asks; "
            "products, quantities and prices are changed by the vendor, who is the seller."
        )
    return None
