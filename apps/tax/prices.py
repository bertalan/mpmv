"""TAX.12: consumer prices include VAT (Directive 98/6/EC, national consumer codes; owner
decision MQ-12, 2026-11-08; note wording and "il prezzo reale", 2026-11-16). The public API of
the displayed price.

Every B2C price the shop shows (cards, product pages, variant prices, cart, feeds, JSON-LD) is
the real price the consumer will pay: the net price plus the VAT the TAX.3 engine decides at
checkout for this vendor and destination (`resolve_goods_tax` on the same context as
`build_tax_context` for a consumer). So an OSS vendor's price carries the destination rate, a
domestic-only vendor's price its own country's rate, and an exempt vendor's (forfettario, art. 74
books, other exemptions) carries no VAT: a rate the vendor will not charge is never added.

Before the buyer gives a delivery address the destination is an estimate: the delivery country
(and postcode) remembered from the checkout, else the active language's default country, else
the operator's country (`current_operator()`, never a literal). Under each price the shop writes
the owner's note `PRICE_NOTE`. A business buyer who opted in (`set_business_net_view`) sees the
net amount with "excl. VAT" instead.

The catalog never imports this module (spec 01 graph): its templates load the
`consumer_prices` template tags, and other adapters (SEO.1 JSON-LD) call `consumer_price`.

    from apps.tax.prices import consumer_price

    price = consumer_price(Decimal("12.50"), "standard", vendor=product.vendor, request=request)
    price.gross     # Decimal("15.25") for an Italian visitor of an OSS vendor (22 %)
    price.rate      # Decimal("22.0000")
    price.country   # "IT", the destination (estimated until the buyer gives an address)
    price.estimated # True until the buyer gives a delivery address

`consumer_price` raises `PriceUnavailable` when no decision can be made (a missing rate, an
unknown fiscal category, a vendor without a tax profile, no platform operator): the caller then
shows no amount, never the net price.
"""

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from django.http import HttpRequest
from django.utils import timezone
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

from apps.core.countries import InvalidCountry, normalize_country
from apps.core.services import current_operator
from apps.core.validity import NoValidRow, OverlappingValidity
from apps.vendors.models import Vendor

from . import engine
from .models import VendorTaxProfile
from .services import (
    NoVendorTaxProfile,
    current_tax_profiles,
    rate_table,
    territory_rules,
    vendor_tax_snapshot,
)

# The owner's wording (2026-11-16) under every consumer price.
PRICE_NOTE = _("Indicative price. Final amount at checkout.")
NET_NOTE = _("excl. VAT")

# Session keys: the delivery address the buyer gave at checkout, and the business opt-in.
DELIVERY_COUNTRY_KEY = "tax_price_country"
DELIVERY_POSTCODE_KEY = "tax_price_postcode"
BUSINESS_NET_VIEW_KEY = "tax_business_net_view"

_REQUEST_CACHE = "_tax_consumer_price_tables"


class PriceUnavailable(Exception):
    """No consumer price can be decided: the price is not shown (never a net price)."""


@dataclass(frozen=True)
class ConsumerPrice:
    """A price as a buyer sees it.

    `gross` is always what a consumer pays (JSON-LD `Offer.price` with
    `valueAddedTaxIncluded: true`); `amount` is what the page shows: `gross`, or `net` for a
    business buyer who opted in. `rate` is 0 for a vendor that charges no VAT.
    """

    net: Decimal
    gross: Decimal
    rate: Decimal  # a percentage, 4 decimals (22.0000)
    country: str  # the destination (delivery or estimated) country
    estimated: bool  # True until the buyer gives a delivery address
    business_net_view: bool = False

    @property
    def amount(self) -> Decimal:
        return self.net if self.business_net_view else self.gross

    @property
    def vat_included(self) -> bool:
        return not self.business_net_view

    @property
    def note(self) -> str:
        return str(NET_NOTE if self.business_net_view else PRICE_NOTE)


@dataclass
class _Tables:
    at: dt.datetime
    rates: engine.RateTable
    territories: tuple[engine.TerritoryRule, ...]
    operator_country: str
    profiles: dict[int, VendorTaxProfile] | None = None  # every vendor's, read on first use
    vendors: dict[int, engine.VendorTaxProfileSnapshot | Exception] = field(default_factory=dict)


def _session(request: HttpRequest | None) -> Any:
    return getattr(request, "session", None) if request is not None else None


def remember_delivery_address(request: HttpRequest, country: str, postcode: str = "") -> None:
    """The checkout calls this when the buyer gives a delivery address: from then on the shop's
    prices use that destination (a postcode finds Livigno, Campione, the Canaries and the other
    territories) and are no longer an estimate."""
    request.session[DELIVERY_COUNTRY_KEY] = country
    request.session[DELIVERY_POSTCODE_KEY] = postcode


def set_business_net_view(request: HttpRequest, enabled: bool) -> None:
    """The business buyer's opt-in to net prices (MQ-12). There is no business toggle in the
    shop yet: this is the hook it will call; nothing else turns the net view on."""
    if enabled:
        request.session[BUSINESS_NET_VIEW_KEY] = True
    else:
        request.session.pop(BUSINESS_NET_VIEW_KEY, None)


def business_net_view(request: HttpRequest | None) -> bool:
    session = _session(request)
    return bool(session is not None and session.get(BUSINESS_NET_VIEW_KEY) is True)


def _tables(request: HttpRequest | None, at: dt.datetime | None) -> _Tables:
    cached = getattr(request, _REQUEST_CACHE, None) if request is not None and at is None else None
    if isinstance(cached, _Tables):
        return cached
    if isinstance(cached, PriceUnavailable):  # no operator: asked once per request, not per card
        raise cached
    keep = request is not None and at is None
    when = at or timezone.now()
    try:
        operator_country = current_operator(when).country
    except (NoValidRow, OverlappingValidity) as exc:
        error = PriceUnavailable(str(exc))
        if keep:
            setattr(request, _REQUEST_CACHE, error)
        raise error from exc
    tables = _Tables(when, rate_table(when), tuple(territory_rules()), operator_country)
    if keep:
        setattr(request, _REQUEST_CACHE, tables)
    return tables


def _vendor_profile(tables: _Tables, vendor: Vendor) -> engine.VendorTaxProfileSnapshot:
    """The vendor's profile; all profiles are read once per request (a grid of many vendors)."""
    known = tables.vendors.get(vendor.pk)
    if known is None:
        if tables.profiles is None:
            tables.profiles = current_tax_profiles(tables.at)
        profile = tables.profiles.get(vendor.pk)
        try:
            if profile is None:
                raise NoVendorTaxProfile(f"vendor {vendor.public_id} has no valid tax profile")
            known = vendor_tax_snapshot(vendor, tables.at, tables.territories, profile=profile)
        except (NoValidRow, engine.BlockingTaxError) as exc:
            known = exc
        tables.vendors[vendor.pk] = known
    if isinstance(known, Exception):
        raise PriceUnavailable(str(known)) from known
    return known


def estimated_destination(
    request: HttpRequest | None = None, *, at: dt.datetime | None = None
) -> tuple[str, str, bool]:
    """(country, postcode, estimated): the destination a displayed price uses for this visitor."""
    session = _session(request)
    remembered = session.get(DELIVERY_COUNTRY_KEY, "") if session is not None else ""
    country, estimated = engine.estimate_price_country(
        delivery_country=remembered if isinstance(remembered, str) else "",
        language=get_language() or "",
        operator_country=_tables(request, at).operator_country,
    )
    postcode: object = ""
    if session is not None and not estimated:
        postcode = session.get(DELIVERY_POSTCODE_KEY, "")
    return country, postcode if isinstance(postcode, str) else "", estimated


def estimated_country(
    request: HttpRequest | None = None, *, at: dt.datetime | None = None
) -> tuple[str, bool]:
    """The country a displayed price uses for this visitor, and whether it is an estimate."""
    country, _postcode, estimated = estimated_destination(request, at=at)
    return country, estimated


def consumer_price(
    net: Decimal,
    fiscal_category: str,
    *,
    vendor: Vendor,
    request: HttpRequest | None = None,
    country: str | None = None,
    postcode: str | None = None,
    at: dt.datetime | None = None,
) -> ConsumerPrice:
    """The price of one unit (or one line) of `vendor`'s product for a consumer: `net` plus the
    VAT checkout will charge for this vendor and destination, ROUND_HALF_UP to the cent.

    `country` (and `postcode`) force the destination (not an estimate); without it the
    destination comes from `request` (`estimated_destination`). With a `request`, a business
    buyer who opted in gets `business_net_view` (the page shows the net amount); `gross` stays
    the consumer price for machine-readable prices. Rates, territories and vendor profiles are
    read once per request.
    """
    if not isinstance(net, Decimal):
        raise TypeError("net must be a Decimal")
    tables = _tables(request, at)
    if country is not None:
        chosen, where, estimated = country, postcode or "", False
    else:
        chosen, where, estimated = estimated_destination(request, at=at)
    profile = _vendor_profile(tables, vendor)
    try:
        ctx = engine.consumer_context(
            vendor.country,
            profile,
            destination_country=chosen,
            destination_postcode=where or None,
            rates=tables.rates,
            on=tables.at.date(),
            platform_country=tables.operator_country,
            territories=tables.territories,
        )
        decision = engine.resolve_goods_tax(ctx, fiscal_category)
        destination = normalize_country(chosen)
    except (engine.BlockingTaxError, InvalidCountry, ValueError) as exc:
        raise PriceUnavailable(str(exc)) from exc
    if engine.is_northern_ireland(destination, where):
        destination = engine.NORTHERN_IRELAND_CODE
    return ConsumerPrice(
        net=net,
        gross=engine.gross_amount(net, decision.rate),
        rate=decision.rate,
        country=destination,
        estimated=estimated,
        business_net_view=business_net_view(request),
    )
