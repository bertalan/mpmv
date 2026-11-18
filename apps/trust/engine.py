"""The trust engine (TRU.1, spec 16 §16.4): vendor badges 1 to 7 as a pure function.

`compute_vendor_trust(inputs, rules, previous)` takes facts that `trust.services` (TRU.2) builds
from orders, returns, reviews and the vendor, with the §16.5 exclusions already applied, and
returns the badges with the figure behind each and, for a missing badge, the reason. No ORM, no
clock, no Django (ARC.18): the moment is `inputs.as_of`. Badge 8 (business verified) is TRU.9.

Thresholds are data (`TrustRulesData`, stored as versioned `TrustRules`). The rate badges (5 and
6) have hysteresis: `previous` says which badges the vendor held in its last snapshot; a held
badge is kept until the figure crosses the lose threshold, which is looser than the earn one.
"""

import datetime as dt
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final

BADGE_ORDERS: Final = 1
BADGE_REVIEWS: Final = 2
BADGE_COUNTRIES: Final = 3
BADGE_REPEAT: Final = 4
BADGE_DISPATCH: Final = 5
BADGE_DISPUTES: Final = 6
BADGE_SINCE: Final = 7

BADGE_CODES: Final = {
    BADGE_ORDERS: "orders_delivered",
    BADGE_REVIEWS: "reviews_received",
    BADGE_COUNTRIES: "countries_delivered_to",
    BADGE_REPEAT: "repeat_customers",
    BADGE_DISPATCH: "dispatch_on_time",
    BADGE_DISPUTES: "low_dispute_rate",
    BADGE_SINCE: "on_mpmv_since",
}

# Why a badge is missing (shown to the vendor, never as a penalty).
NOT_ENOUGH_ORDERS: Final = "not_enough_orders"
BELOW_THRESHOLD: Final = "below_threshold"
NO_REVIEWS: Final = "no_reviews"
NO_DATA: Final = "no_data"

# Upheld dispute kinds (spec 16 §16.4 badge 6). A withdrawal is never one: penalising a legal
# right would discourage it (§16.11 case 4).
DISPUTE_KINDS: Final = frozenset(
    {"warranty", "not_as_described", "payment_dispute", "notice_upheld"}
)
WITHDRAWAL: Final = "withdrawal"

EXCERPT_LENGTH: Final = 200
HUNDRED: Final = Decimal(100)
TWO_PLACES: Final = Decimal("0.01")


@dataclass(frozen=True)
class TrustRulesData:
    """The thresholds of one rules version (the defaults are the spec 16 §16.4 table)."""

    version: int
    # Badge 1
    orders_tiers: tuple[int, ...] = (10, 50, 100, 500, 1000)
    min_order_value: Decimal = Decimal("5.00")
    withdrawal_window_days: int = 14
    orders_per_buyer_cap: int = 3
    orders_cap_window_days: int = 365
    # Badge 2
    reviews_stars_from: int = 5
    review_excerpt_length: int = EXCERPT_LENGTH
    # Badge 3
    countries_min_orders: int = 2
    countries_min_buyers: int = 2
    # Badge 4
    repeat_window_days: int = 730
    repeat_min_gap_days: int = 7
    repeat_shown_from: int = 3
    # Badge 5
    dispatch_window_days: int = 90
    dispatch_min_orders: int = 10
    dispatch_earn_percent: Decimal = Decimal(90)
    dispatch_lose_percent: Decimal = Decimal(85)
    # Badge 6
    dispute_window_days: int = 365
    dispute_min_orders: int = 20
    dispute_earn_percent: Decimal = Decimal(3)
    dispute_lose_percent: Decimal = Decimal(4)


PARAMETER_NAMES: Final = tuple(f.name for f in fields(TrustRulesData) if f.name != "version")


def rules_to_params(rules: TrustRulesData) -> dict[str, Any]:
    """JSON-safe thresholds (decimals as strings, tiers as a list), without the version."""
    params: dict[str, Any] = {}
    for name in PARAMETER_NAMES:
        value = getattr(rules, name)
        if isinstance(value, Decimal):
            params[name] = str(value)
        elif isinstance(value, tuple):
            params[name] = list(value)
        else:
            params[name] = value
    return params


def rules_from_params(version: int, params: Mapping[str, Any]) -> TrustRulesData:
    """The inverse of `rules_to_params`. Raises `ValueError` for an unknown or malformed value;
    a missing name takes the spec default."""
    unknown = sorted(set(params) - set(PARAMETER_NAMES))
    if unknown:
        raise ValueError(f"unknown trust rule parameters: {', '.join(unknown)}")
    defaults = TrustRulesData(version=version)
    values: dict[str, Any] = {}
    for name in PARAMETER_NAMES:
        default = getattr(defaults, name)
        value = params.get(name, default)
        try:
            if isinstance(default, Decimal):
                if isinstance(value, bool | float):
                    raise TypeError("a decimal is written as a string")
                value = Decimal(str(value))
            elif isinstance(default, tuple):
                value = tuple(int(v) for v in value)
            else:
                if isinstance(value, bool) or not isinstance(value, int):
                    raise TypeError("a whole number")
        except (TypeError, ValueError, ArithmeticError) as exc:
            raise ValueError(f"{name}: {exc}") from None
        values[name] = value
    return TrustRulesData(version=version, **values)


def rules_errors(rules: TrustRulesData) -> dict[str, str]:
    """Problems with a rules version, by parameter name; empty when it is valid."""
    errors: dict[str, str] = {}
    if rules.version < 1:
        errors["version"] = "A version counts from 1."
    tiers = rules.orders_tiers
    if not tiers or any(t < 1 for t in tiers) or list(tiers) != sorted(set(tiers)):
        errors["orders_tiers"] = "Tiers are positive and strictly ascending."
    if rules.min_order_value < 0:
        errors["min_order_value"] = "Cannot be negative."
    for name in (
        "withdrawal_window_days",
        "orders_per_buyer_cap",
        "orders_cap_window_days",
        "reviews_stars_from",
        "countries_min_orders",
        "countries_min_buyers",
        "repeat_window_days",
        "repeat_min_gap_days",
        "repeat_shown_from",
        "dispatch_window_days",
        "dispatch_min_orders",
        "dispute_window_days",
        "dispute_min_orders",
    ):
        if getattr(rules, name) < 1:
            errors[name] = "Must be at least 1."
    if not 1 <= rules.review_excerpt_length <= 1000:
        errors["review_excerpt_length"] = "Between 1 and 1000 characters."
    for name in ("dispatch_earn_percent", "dispatch_lose_percent"):
        if not 0 <= getattr(rules, name) <= 100:
            errors[name] = "A percentage between 0 and 100."
    for name in ("dispute_earn_percent", "dispute_lose_percent"):
        if not 0 <= getattr(rules, name) <= 100:
            errors[name] = "A percentage between 0 and 100."
    if (
        "dispatch_lose_percent" not in errors
        and rules.dispatch_lose_percent > rules.dispatch_earn_percent
    ):
        errors["dispatch_lose_percent"] = "The lose threshold cannot be above the earn threshold."
    if (
        "dispute_lose_percent" not in errors
        and rules.dispute_lose_percent < rules.dispute_earn_percent
    ):
        errors["dispute_lose_percent"] = "The lose threshold cannot be below the earn threshold."
    return errors


@dataclass(frozen=True)
class DeliveredOrder:
    """A vendor order in `delivered`, after the §16.5 exclusions. `buyer` is an opaque key."""

    ref: str
    buyer: str
    delivered_at: dt.datetime
    net_goods: Decimal
    country: str
    wholly_refunded: bool = False


@dataclass(frozen=True)
class PublishedReview:
    ref: str
    published_at: dt.datetime
    rating: int
    excerpt: str = ""


@dataclass(frozen=True)
class Dispatch:
    """An order shipped; `on_time` is decided by the caller against the date promised at
    checkout, with the clock paused during a declared closure."""

    ref: str
    shipped_at: dt.datetime
    on_time: bool


@dataclass(frozen=True)
class DisputeFact:
    """A return, payment dispute or notice a staff member upheld, or a withdrawal (not counted)."""

    ref: str
    occurred_at: dt.datetime
    kind: str


@dataclass(frozen=True)
class VendorTrustInputs:
    vendor_ref: str
    as_of: dt.datetime
    delivered_orders: tuple[DeliveredOrder, ...] = ()
    reviews: tuple[PublishedReview, ...] = ()
    dispatches: tuple[Dispatch, ...] = ()
    disputes: tuple[DisputeFact, ...] = ()
    first_activated_at: dt.datetime | None = None


@dataclass(frozen=True)
class BadgeResult:
    number: int
    code: str
    earned: bool
    tier: int | None = None
    figures: dict[str, Any] = field(default_factory=dict)
    # What a public page may show. Empty for badge 6: never a number, only the badge.
    public_figures: dict[str, Any] = field(default_factory=dict)
    missing: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "code": self.code,
            "earned": self.earned,
            "tier": self.tier,
            "figures": self.figures,
            "public_figures": self.public_figures,
            "missing": self.missing,
        }


@dataclass(frozen=True)
class VendorTrustResult:
    as_of: dt.datetime
    rules_version: int
    badges: tuple[BadgeResult, ...]

    @property
    def earned(self) -> dict[int, bool]:
        """The `previous` argument of the next computation."""
        return {b.number: b.earned for b in self.badges}

    @property
    def metrics(self) -> dict[str, Any]:
        return {b.code: b.figures for b in self.badges}

    def to_json(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat(),
            "rules_version": self.rules_version,
            "badges": [b.to_json() for b in self.badges],
        }


def compute_vendor_trust(
    inputs: VendorTrustInputs,
    rules: TrustRulesData,
    previous: Mapping[int, bool] | None = None,
) -> VendorTrustResult:
    """Badges 1 to 7 for one vendor at `inputs.as_of`, under `rules`.

    `previous` maps a badge number to whether the vendor held it in its last snapshot (None:
    no snapshot yet), for the hysteresis of badges 5 and 6.
    """
    held = previous or {}
    for fact in inputs.disputes:
        if fact.kind != WITHDRAWAL and fact.kind not in DISPUTE_KINDS:
            raise ValueError(f"unknown dispute kind: {fact.kind!r}")
    counted = _counted_orders(inputs, rules)
    badges = (
        _orders_badge(inputs, rules, counted),
        _reviews_badge(inputs, rules),
        _countries_badge(rules, counted),
        _repeat_badge(inputs, rules, counted),
        _dispatch_badge(inputs, rules, held.get(BADGE_DISPATCH, False)),
        _disputes_badge(inputs, rules, held.get(BADGE_DISPUTES, False)),
        _since_badge(inputs),
    )
    return VendorTrustResult(as_of=inputs.as_of, rules_version=rules.version, badges=badges)


def _days_before(as_of: dt.datetime, days: int) -> dt.datetime:
    return as_of - dt.timedelta(days=days)


def _counted_orders(inputs: VendorTrustInputs, rules: TrustRulesData) -> list[DeliveredOrder]:
    """Delivered orders whose withdrawal window has closed and that were not wholly refunded."""
    closed_before = _days_before(inputs.as_of, rules.withdrawal_window_days)
    return sorted(
        (
            o
            for o in inputs.delivered_orders
            if o.delivered_at <= closed_before and not o.wholly_refunded
        ),
        key=lambda o: (o.delivered_at, o.ref),
    )


def _orders_badge(
    inputs: VendorTrustInputs, rules: TrustRulesData, counted: list[DeliveredOrder]
) -> BadgeResult:
    window = dt.timedelta(days=rules.orders_cap_window_days)
    accepted: dict[str, list[dt.datetime]] = defaultdict(list)
    count = 0
    for order in counted:
        if order.net_goods < rules.min_order_value:
            continue
        recent = [t for t in accepted[order.buyer] if order.delivered_at - t < window]
        if len(recent) >= rules.orders_per_buyer_cap:
            continue
        accepted[order.buyer].append(order.delivered_at)
        count += 1
    tier = max((t for t in rules.orders_tiers if count >= t), default=None)
    figures: dict[str, Any] = {"count": count, "tier": tier, "next_tier": _next(rules, count)}
    return BadgeResult(
        BADGE_ORDERS,
        BADGE_CODES[BADGE_ORDERS],
        tier is not None,
        tier,
        figures,
        {"tier": tier, "as_of": inputs.as_of.date().isoformat()},
        "" if tier is not None else NOT_ENOUGH_ORDERS,
    )


def _next(rules: TrustRulesData, count: int) -> int | None:
    return min((t for t in rules.orders_tiers if t > count), default=None)


def _reviews_badge(inputs: VendorTrustInputs, rules: TrustRulesData) -> BadgeResult:
    reviews = inputs.reviews
    if not reviews:
        return BadgeResult(
            BADGE_REVIEWS,
            BADGE_CODES[BADGE_REVIEWS],
            False,
            figures={"count": 0, "stars_shown": False},
            missing=NO_REVIEWS,
        )
    mean = (Decimal(sum(r.rating for r in reviews)) / len(reviews)).quantize(
        TWO_PLACES, ROUND_HALF_UP
    )
    # The latest, never the best: the vendor cannot pick (§16.11 case 6).
    latest = max(reviews, key=lambda r: (r.published_at, r.ref))
    stars_shown = len(reviews) >= rules.reviews_stars_from
    figures = {
        "count": len(reviews),
        "mean": str(mean),
        "stars_shown": stars_shown,
        "latest": {
            "ref": latest.ref,
            "rating": latest.rating,
            "excerpt": latest.excerpt[: rules.review_excerpt_length],
            "published_at": latest.published_at.isoformat(),
        },
    }
    public = {"count": len(reviews), "latest": figures["latest"]}
    if stars_shown:
        public["mean"] = str(mean)
    return BadgeResult(BADGE_REVIEWS, BADGE_CODES[BADGE_REVIEWS], True, None, figures, public)


def _countries_badge(rules: TrustRulesData, counted: list[DeliveredOrder]) -> BadgeResult:
    orders: dict[str, int] = defaultdict(int)
    buyers: dict[str, set[str]] = defaultdict(set)
    for order in counted:
        orders[order.country] += 1
        buyers[order.country].add(order.buyer)
    countries = sorted(
        c
        for c in orders
        if orders[c] >= rules.countries_min_orders and len(buyers[c]) >= rules.countries_min_buyers
    )
    figures = {"count": len(countries), "countries": countries}
    return BadgeResult(
        BADGE_COUNTRIES,
        BADGE_CODES[BADGE_COUNTRIES],
        bool(countries),
        None,
        figures,
        dict(figures),
        "" if countries else NO_DATA,
    )


def _repeat_badge(
    inputs: VendorTrustInputs, rules: TrustRulesData, counted: list[DeliveredOrder]
) -> BadgeResult:
    since = _days_before(inputs.as_of, rules.repeat_window_days)
    dates: dict[str, list[dt.datetime]] = defaultdict(list)
    for order in counted:
        if order.delivered_at >= since:
            dates[order.buyer].append(order.delivered_at)
    gap = dt.timedelta(days=rules.repeat_min_gap_days)
    count = sum(1 for times in dates.values() if len(times) >= 2 and max(times) - min(times) >= gap)
    earned = count >= rules.repeat_shown_from
    figures = {"count": count}
    return BadgeResult(
        BADGE_REPEAT,
        BADGE_CODES[BADGE_REPEAT],
        earned,
        None,
        figures,
        dict(figures) if earned else {},
        "" if earned else BELOW_THRESHOLD,
    )


def _rate_badge(
    number: int,
    *,
    ok: int,
    total: int,
    min_total: int,
    earn: Decimal,
    lose: Decimal,
    held: bool,
    higher_is_better: bool,
    figures: dict[str, Any],
) -> BadgeResult:
    """Earn/lose with hysteresis on a percentage of `total`, compared exactly (no rounding)."""
    code = BADGE_CODES[number]
    if total < min_total:
        return BadgeResult(number, code, False, None, figures, {}, NOT_ENOUGH_ORDERS)
    scaled = ok * HUNDRED  # compare ok / total with a percentage without dividing
    if higher_is_better:
        earned = scaled >= (lose if held else earn) * total
    else:
        earned = scaled <= lose * total if held else scaled < earn * total
    return BadgeResult(number, code, earned, None, figures, {}, "" if earned else BELOW_THRESHOLD)


def _percent(part: int, total: int) -> str | None:
    if not total:
        return None
    return str((Decimal(part) * HUNDRED / total).quantize(TWO_PLACES, ROUND_HALF_UP))


def _dispatch_badge(inputs: VendorTrustInputs, rules: TrustRulesData, held: bool) -> BadgeResult:
    since = _days_before(inputs.as_of, rules.dispatch_window_days)
    recent = [d for d in inputs.dispatches if since <= d.shipped_at <= inputs.as_of]
    on_time = sum(1 for d in recent if d.on_time)
    figures = {"orders": len(recent), "on_time": on_time, "percent": _percent(on_time, len(recent))}
    badge = _rate_badge(
        BADGE_DISPATCH,
        ok=on_time,
        total=len(recent),
        min_total=rules.dispatch_min_orders,
        earn=rules.dispatch_earn_percent,
        lose=rules.dispatch_lose_percent,
        held=held,
        higher_is_better=True,
        figures=figures,
    )
    return _with_public(badge, {"window_days": rules.dispatch_window_days})


def _disputes_badge(inputs: VendorTrustInputs, rules: TrustRulesData, held: bool) -> BadgeResult:
    since = _days_before(inputs.as_of, rules.dispute_window_days)
    orders = [o for o in _counted_orders(inputs, rules) if o.delivered_at >= since]
    upheld = sum(
        1
        for d in inputs.disputes
        if d.kind in DISPUTE_KINDS and since <= d.occurred_at <= inputs.as_of
    )
    figures = {
        "delivered_orders": len(orders),
        "upheld_disputes": upheld,
        "percent": _percent(upheld, len(orders)),
    }
    # Fewer is better: `ok` is the upheld count and the thresholds are ceilings.
    return _rate_badge(
        BADGE_DISPUTES,
        ok=upheld,
        total=len(orders),
        min_total=rules.dispute_min_orders,
        earn=rules.dispute_earn_percent,
        lose=rules.dispute_lose_percent,
        held=held,
        higher_is_better=False,
        figures=figures,
    )  # public_figures stay empty: never shown as a number


def _with_public(badge: BadgeResult, public: dict[str, Any]) -> BadgeResult:
    if not badge.earned:
        return badge
    return BadgeResult(
        badge.number, badge.code, True, badge.tier, badge.figures, public, badge.missing
    )


def _since_badge(inputs: VendorTrustInputs) -> BadgeResult:
    first = inputs.first_activated_at
    if first is None:
        return BadgeResult(BADGE_SINCE, BADGE_CODES[BADGE_SINCE], False, missing=NO_DATA)
    figures = {"since": first.year}
    return BadgeResult(BADGE_SINCE, BADGE_CODES[BADGE_SINCE], True, None, figures, dict(figures))
