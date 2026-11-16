"""TAX.12: consumer prices include VAT (Directive 98/6/EC, national consumer codes; owner
decision MQ-12, 2026-11-08, note wording 2026-11-16). The public API of the displayed price.

Every B2C price the shop shows (cards, product pages, variant prices, cart, feeds, JSON-LD) is
the gross amount at the rate of the delivery country, decided by the TAX.3 engine
(`engine.consumer_tax_decision`). Before the buyer gives a delivery country the country is an
estimate: the delivery country remembered from the checkout, else the active language's default
country, else the operator's country (`current_operator()`, never a literal). Under each price
the shop writes the owner's note `PRICE_NOTE`. A business buyer who opted in
(`set_business_net_view`) sees the net amount with "excl. VAT" instead.

The catalog never imports this module (spec 01 graph): its templates load the
`consumer_prices` template tags, and other adapters (SEO.1 JSON-LD) call `consumer_price`.

    from apps.tax.prices import consumer_price

    price = consumer_price(Decimal("12.50"), "standard", request=request)
    price.gross     # Decimal("15.25") for an Italian visitor (22 %)
    price.rate      # Decimal("22.0000")
    price.country   # "IT"
    price.estimated # True until the buyer gives a delivery country

`consumer_price` raises `PriceUnavailable` when no rate can be decided (a missing rate, an
unknown fiscal category, no platform operator): the caller then shows no amount.
"""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.http import HttpRequest
from django.utils import timezone
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

from apps.core.countries import InvalidCountry, normalize_country
from apps.core.services import current_operator
from apps.core.validity import NoValidRow, OverlappingValidity

from . import engine
from .services import rate_table, territory_rules

# The owner's wording (2026-11-16) under every consumer price.
PRICE_NOTE = _("Indicative price. Final amount at checkout.")
NET_NOTE = _("excl. VAT")

# Session keys: the delivery country the buyer gave at checkout, and the business opt-in.
DELIVERY_COUNTRY_KEY = "tax_price_country"
BUSINESS_NET_VIEW_KEY = "tax_business_net_view"

_REQUEST_CACHE = "_tax_consumer_price_tables"


class PriceUnavailable(Exception):
    """No consumer rate can be decided: the price is not shown (never a net price)."""


@dataclass(frozen=True)
class ConsumerPrice:
    """A price as a buyer sees it.

    `gross` is always VAT-inclusive (JSON-LD `Offer.price` with `valueAddedTaxIncluded: true`);
    `amount` is what the page shows: `gross`, or `net` for a business buyer who opted in.
    """

    net: Decimal
    gross: Decimal
    rate: Decimal  # a percentage, 4 decimals (22.0000)
    country: str  # the delivery country, or the estimated one
    estimated: bool  # True until the buyer gives a delivery country
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


@dataclass(frozen=True)
class _Tables:
    at: dt.datetime
    rates: engine.RateTable
    territories: tuple[engine.TerritoryRule, ...]
    operator_country: str


def _session(request: HttpRequest | None) -> Any:
    return getattr(request, "session", None) if request is not None else None


def remember_delivery_country(request: HttpRequest, country: str) -> None:
    """The checkout calls this when the buyer gives a delivery address: from then on the shop's
    prices use that country's rate and are no longer an estimate."""
    request.session[DELIVERY_COUNTRY_KEY] = country


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


def estimated_country(
    request: HttpRequest | None = None, *, at: dt.datetime | None = None
) -> tuple[str, bool]:
    """The country a displayed price uses for this visitor, and whether it is an estimate."""
    session = _session(request)
    remembered = session.get(DELIVERY_COUNTRY_KEY, "") if session is not None else ""
    return engine.estimate_price_country(
        delivery_country=remembered if isinstance(remembered, str) else "",
        language=get_language() or "",
        operator_country=_tables(request, at).operator_country,
    )


def consumer_price(
    net: Decimal,
    fiscal_category: str,
    *,
    request: HttpRequest | None = None,
    country: str | None = None,
    postcode: str | None = None,
    at: dt.datetime | None = None,
) -> ConsumerPrice:
    """The price of one unit (or one line) for a consumer: `net` plus the VAT of the delivery
    country, ROUND_HALF_UP to the cent.

    `country` forces the delivery country (not an estimate); without it the country comes from
    `request` (`estimated_country`). With a `request`, a business buyer who opted in gets
    `business_net_view` (the page shows the net amount); `gross` stays VAT-inclusive for
    machine-readable prices. The rate tables are read once per request.
    """
    if not isinstance(net, Decimal):
        raise TypeError("net must be a Decimal")
    tables = _tables(request, at)
    if country is not None:
        chosen, estimated = country, False
    else:
        chosen, estimated = estimated_country(request, at=at)
    try:
        decision = engine.consumer_tax_decision(
            chosen,
            fiscal_category,
            rates=tables.rates,
            on=tables.at.date(),
            platform_country=tables.operator_country,
            postcode=postcode,
            territories=tables.territories,
        )
    except (engine.BlockingTaxError, InvalidCountry, ValueError) as exc:
        raise PriceUnavailable(str(exc)) from exc
    return ConsumerPrice(
        net=net,
        gross=engine.gross_amount(net, decision.rate),
        rate=decision.rate,
        country=decision.country_of_taxation or normalize_country(chosen),
        estimated=estimated,
        business_net_view=business_net_view(request),
    )
