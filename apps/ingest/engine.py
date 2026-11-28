"""Pure rules of the ingest app (ING.1): run status machine, review decisions, SKU matching and
mapping lookup. No ORM, no Django: the services load the rows and call these functions."""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final, Literal, TypeIs
from urllib.parse import urlsplit, urlunsplit

from apps.catalog.engine import sku_match_key

# --- Import run status machine

QUEUED: Final = "queued"
RUNNING: Final = "running"
SUCCEEDED: Final = "succeeded"
FAILED: Final = "failed"
CANCELLED: Final = "cancelled"

RUN_STATUSES: Final = (QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELLED)
RUN_TRANSITIONS: Final[Mapping[str, frozenset[str]]] = {
    QUEUED: frozenset({RUNNING, CANCELLED}),
    RUNNING: frozenset({SUCCEEDED, FAILED, CANCELLED}),
    SUCCEEDED: frozenset(),
    FAILED: frozenset(),
    CANCELLED: frozenset(),
}
RUN_COUNTERS: Final = ("seen", "created", "updated", "unchanged", "skipped", "errors")


def can_transition_run(source: str, target: str) -> bool:
    return target in RUN_TRANSITIONS.get(source, frozenset())


def is_terminal_run(status: str) -> bool:
    return not RUN_TRANSITIONS.get(status, frozenset())


# --- Review items

PENDING: Final = "pending"
ACCEPTED: Final = "accepted"
REJECTED: Final = "rejected"
DEFERRED: Final = "deferred"

DECISIONS: Final = (PENDING, ACCEPTED, REJECTED, DEFERRED)
DECISION_TRANSITIONS: Final[Mapping[str, frozenset[str]]] = {
    PENDING: frozenset({ACCEPTED, REJECTED, DEFERRED}),
    DEFERRED: frozenset({ACCEPTED, REJECTED}),
    ACCEPTED: frozenset(),
    REJECTED: frozenset(),
}
# Decisions that leave the item open: a blocking item in one of them still stops its run.
OPEN_DECISIONS: Final = frozenset({PENDING, DEFERRED})
BLOCKING_KINDS: Final = frozenset({"duplicate_sku", "identity_change"})


def can_decide(current: str, decision: str) -> bool:
    return decision in DECISION_TRANSITIONS.get(current, frozenset())


def is_blocking(kind: str) -> bool:
    """A review item of this kind stops the run until a person decides (never a guess)."""
    return kind in BLOCKING_KINDS


# --- SKU matching (spec 08, CAT.13)


@dataclass(frozen=True)
class SkuMatch:
    kind: Literal["exact", "alias", "ambiguous", "new"]
    product_ids: tuple[int, ...]


def match_sku(
    key: str, *, by_key: Mapping[str, Sequence[int]], by_alias: Mapping[str, Sequence[int]]
) -> SkuMatch:
    """Match a SKU match key against the vendor's live keys, then its alias history.

    More than one product, whether through the two sources or through the alias history alone
    (SKU-AMBIGUOUS), is `ambiguous`: the caller reports it and writes nothing.
    """
    live = sorted(set(by_key.get(key, ())))
    old = sorted(set(by_alias.get(key, ())))
    if live and set(old) - set(live):
        return SkuMatch("ambiguous", tuple(sorted({*live, *old})))
    if len(live) > 1:
        return SkuMatch("ambiguous", tuple(live))
    if live:
        return SkuMatch("exact", tuple(live))
    if len(old) > 1:
        return SkuMatch("ambiguous", tuple(old))
    if old:
        return SkuMatch("alias", tuple(old))
    return SkuMatch("new", ())


# --- Mapping lookup


class AmbiguousMapping(Exception):
    """Two entries of the same scope and rank answer one raw value."""


@dataclass(frozen=True)
class MappingEntry:
    vendor_id: int | None  # None: valid for every vendor
    kind: str
    key: str
    aliases: tuple[str, ...]
    target_type: str  # "app_label.model" of the target object, or ""
    target_id: int | None
    target_value: str
    ref: int | None = None  # the caller's handle on the row, handed back untouched


def mapping_key(raw: str) -> str:
    """The normal form mappings are matched on (same rules as a SKU match key)."""
    return sku_match_key(raw)


def resolve_mapping(
    entries: Sequence[MappingEntry], *, vendor_id: int | None, kind: str, raw: str
) -> MappingEntry | None:
    """The entry that answers `raw`. Precedence: the vendor's key, the vendor's alias, the
    platform's key, the platform's alias. Two entries at the same rank raise AmbiguousMapping."""
    wanted = mapping_key(raw)
    if not wanted:
        return None
    scopes = [vendor_id, None] if vendor_id is not None else [None]
    for scope in scopes:
        scoped = [e for e in entries if e.kind == kind and e.vendor_id == scope]
        for by_alias in (False, True):
            hits = [
                e
                for e in scoped
                if (
                    wanted in {mapping_key(a) for a in e.aliases}
                    if by_alias
                    else mapping_key(e.key) == wanted
                )
            ]
            if len(hits) > 1:
                raise AmbiguousMapping(f"{len(hits)} {kind} mappings answer {raw!r}")
            if hits:
                return hits[0]
    return None


def payload_hash(payload: Mapping[str, Any]) -> str:
    """Stable digest of an observation payload (key order does not matter)."""
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode()).hexdigest()


# --- Source state, stock state and change history (ING.2)

SIMPLE: Final = "simple"
VARIABLE: Final = "variable"
ITEM_KINDS: Final = (SIMPLE, VARIABLE)
STOCK_OUT: Final = 0
STOCK_IN: Final = 1


class StateError(ValueError):
    """A supplier observation is incomplete or incoherent; nothing may be stored from it."""


def valid_stock_state(status: object, quantity: object) -> bool:
    """Explicit supplier availability: out of stock is exactly 0 units, in stock is more than 0.

    Anything else (a missing, boolean, negative or contradictory pair) is not a stock state, so
    callers fail closed and write no stock from it."""
    if type(status) is not int or type(quantity) is not int:
        return False
    return (status == STOCK_OUT and quantity == 0) or (status == STOCK_IN and quantity > 0)


def require_stock_state(status: object, quantity: object) -> tuple[int, int]:
    if not (
        valid_stock_state(status, quantity)
        and isinstance(status, int)
        and isinstance(quantity, int)
    ):
        raise StateError("Availability is missing or incoherent; no stock update.")
    return status, quantity


def normalize_source_url(url: str, *, hosts: Sequence[str], path_prefix: str = "/") -> str:
    """Canonical identity of a supplier item: https, host from the allowed set, no credentials,
    port, query or fragment, one trailing slash. The identity is the address, never the SKU."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if (
        parts.scheme not in ("http", "https")
        or host not in hosts
        or parts.username
        or parts.password
        or parts.port
        or parts.query
        or not parts.path.startswith(path_prefix)
    ):
        raise StateError("Not a valid supplier item address.")
    return urlunsplit(("https", hosts[0], parts.path.rstrip("/") + "/", "", ""))


def canonical_state(state: Mapping[str, Any]) -> dict[str, Any]:
    """JSON round trip: decimals become exact strings and key order stops mattering."""
    return json.loads(json.dumps(state, default=str, sort_keys=True))  # type: ignore[no-any-return]


def validate_state(state: Mapping[str, Any]) -> dict[str, Any]:
    """Check a parsed observation and return its canonical form (raises `StateError`).

    A *variable* item has a native id and children, each with its own native id, and carries no
    stock of its own: stock belongs to the children. A *simple* item has no children and, when it
    states availability, a coherent pair. Children state availability or stay unknown."""
    value = canonical_state(state)
    kind = value.get("kind")
    native_id = value.get("native_id")
    children = value.get("variations", {})
    if kind not in ITEM_KINDS or not value.get("sku") or value["sku"] == "N/D":
        raise StateError("Item identity is not valid.")
    if native_id is not None and (type(native_id) is not int or native_id <= 0):
        raise StateError("Native id is not valid.")
    if not isinstance(children, dict):
        raise StateError("Variations must be a mapping.")
    value["variations"] = children
    status, quantity = value.get("stock_status"), value.get("stock_quantity")
    if kind == VARIABLE:
        if native_id is None or not children or status is not None or quantity is not None:
            raise StateError("A variable item needs a native id and children, and no own stock.")
    else:
        if children:
            raise StateError("A simple item has no variations.")
        if (status is not None or quantity is not None) and not valid_stock_state(status, quantity):
            raise StateError("Availability is incoherent.")
    seen: set[int] = set()
    for key, child in children.items():
        child_id = child.get("native_id")
        if type(child_id) is not int or child_id <= 0 or key != str(child_id) or child_id in seen:
            raise StateError("Variation id is not valid or is duplicated.")
        seen.add(child_id)
        c_status, c_quantity = child.get("stock_status"), child.get("stock_quantity")
        if (c_status is not None or c_quantity is not None) and not valid_stock_state(
            c_status, c_quantity
        ):
            raise StateError("Variation availability is incoherent.")
        child["present"] = True
    return value


def merge_state(old: Mapping[str, Any] | None, new: Mapping[str, Any]) -> dict[str, Any]:
    """Fold a validated observation into the stored state.

    Children the supplier no longer lists are kept as `present: false` with their last known
    data (absence is never zero stock), and observed addresses accumulate as evidence."""
    merged = dict(new)
    children = dict(new["variations"])
    urls = set(new.get("source_urls", []))
    if old:
        if old.get("native_id") is not None and old["native_id"] != new.get("native_id"):
            raise StateError("The native id of the parent changed: reconciliation required.")
        for key, child in old.get("variations", {}).items():
            if key not in children:
                children[key] = {**child, "present": False}
        urls |= set(old.get("source_urls", []))
    merged["variations"] = children
    merged["source_urls"] = sorted(urls)
    return merged


def changed_fields(old: Mapping[str, Any] | None, new: Mapping[str, Any]) -> dict[str, Any]:
    """Top-level fields of `new` that differ from `old`, with their new values."""
    old = old or {}
    return {key: value for key, value in new.items() if key not in old or old[key] != value}


def latest_field_values(
    rows: Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]],
    fields: Sequence[str] | None = None,
) -> dict[str, Any]:
    """The last value of each field that a change flagged, from `(changed_fields, new_values)`
    rows in chronological order (version, then id)."""
    latest: dict[str, Any] = {}
    for flagged, values in rows:
        for field, value in values.items():
            if flagged.get(field) is True and (fields is None or field in fields):
                latest[field] = value
    return latest


# --- Stock and price plans (ING.8)

SET_STOCK: Final = "set_stock"
SET_PRICE: Final = "set_price"
UNCHANGED: Final = "unchanged"
SKIP: Final = "skip"
REVIEW: Final = "review"

type StockKind = Literal["set_stock", "unchanged", "skip", "review"]
type PriceKind = Literal["set_price", "unchanged", "skip", "review"]
type StockReason = Literal[
    "unlinked", "stale", "missing", "supplier", "unreliable_status", "incoherent_stock"
]
type PriceReason = Literal[
    "unlinked",
    "stale",
    "invalid_price",
    "invalid_sale_price",
    "sale",
    "same",
    "first_price",
    "price_jump",
    "within_limit",
]
type ReviewKindName = Literal["stale", "price_jump", "sale"]

MISSING_STATUSES: Final = frozenset({404})
DEFAULT_MAX_CHANGE_PCT: Final = Decimal("15")
_CENT: Final = Decimal("0.01")


@dataclass(frozen=True)
class StockObservation:
    """What the supplier said about one item. `missing_status` is the HTTP status of a page that
    no longer answers (404); then no availability is known."""

    ref: int  # the caller's handle (the source item), handed back untouched
    product_id: int | None
    observed_at: datetime
    stock_status: object = None
    stock_quantity: object = None
    missing_status: int | None = None


@dataclass(frozen=True)
class StockProduct:
    product_id: int
    stock_qty: int


@dataclass(frozen=True)
class StockAction:
    """`kind` is set_stock (write `quantity`), unchanged, skip (nothing written) or review.
    A 404 is a `set_stock` of 0 and never an unpublish: the page stays published (SEO)."""

    ref: int
    product_id: int | None
    kind: StockKind
    reason: StockReason
    quantity: int | None = None
    review_kind: ReviewKindName | None = None


def is_stale(observed_at: datetime, *, now: datetime, max_age: timedelta) -> bool:
    return now - observed_at > max_age


def plan_stock_actions(
    observations: Sequence[StockObservation],
    products: Mapping[int, StockProduct],
    *,
    now: datetime,
    max_age: timedelta,
) -> list[StockAction]:
    """One action per observation, in order. The supplier is authoritative for stock (exact
    quantity); a stale observation is flagged for review and never applied; a 404 sets stock to 0;
    an incomplete or incoherent availability is skipped (fail closed, never a guess)."""
    plan: list[StockAction] = []
    for obs in observations:
        product = products.get(obs.product_id) if obs.product_id is not None else None
        if product is None:
            plan.append(StockAction(obs.ref, obs.product_id, SKIP, "unlinked"))
        elif is_stale(obs.observed_at, now=now, max_age=max_age):
            plan.append(StockAction(obs.ref, product.product_id, REVIEW, "stale", None, "stale"))
        elif obs.missing_status is not None:
            if obs.missing_status in MISSING_STATUSES:
                plan.append(_stock_to(obs, product, 0, "missing"))
            else:
                plan.append(StockAction(obs.ref, product.product_id, SKIP, "unreliable_status"))
        elif not (
            valid_stock_state(obs.stock_status, obs.stock_quantity)
            and isinstance(obs.stock_quantity, int)
        ):
            plan.append(StockAction(obs.ref, product.product_id, SKIP, "incoherent_stock"))
        else:
            plan.append(_stock_to(obs, product, obs.stock_quantity, "supplier"))
    return plan


def _stock_to(
    obs: StockObservation, product: StockProduct, quantity: int, reason: StockReason
) -> StockAction:
    if product.stock_qty == quantity:
        return StockAction(obs.ref, product.product_id, UNCHANGED, reason, quantity)
    return StockAction(obs.ref, product.product_id, SET_STOCK, reason, quantity)


@dataclass(frozen=True)
class PriceObservation:
    """Supplier gross prices (VAT included); a `sale_price_gross` means the item is on sale."""

    ref: int
    product_id: int | None
    observed_at: datetime
    price_gross: Decimal | None
    sale_price_gross: Decimal | None = None


@dataclass(frozen=True)
class PriceProduct:
    product_id: int
    price_net: Decimal | None


@dataclass(frozen=True)
class PriceAction:
    """`price_net` is the proposed net price; for a review it is held back, not applied."""

    ref: int
    product_id: int | None
    kind: PriceKind
    reason: PriceReason
    price_net: Decimal | None = None
    old_net: Decimal | None = None
    change_pct: Decimal | None = None
    review_kind: ReviewKindName | None = None


def net_from_gross(gross: Decimal, *, vat_rate: Decimal) -> Decimal:
    """Net price = gross / (1 + VAT rate), rounded half up to the cent.

    The rate is never a literal here: the caller (the service) derives it from `apps.tax` for the
    product's tax category and the vendor's tax position at the observation time."""
    if not vat_rate.is_finite() or vat_rate < 0:
        raise ValueError("The VAT rate must be a finite fraction of at least 0.")
    return (gross / (1 + vat_rate)).quantize(_CENT, rounding=ROUND_HALF_UP)


def change_pct(old: Decimal, new: Decimal) -> Decimal:
    """Absolute change of `new` against `old`, in percent (2 places, half up): for display only,
    never compare it with a limit (see `exceeds_change_limit`)."""
    return (abs(new - old) / old * 100).quantize(_CENT, rounding=ROUND_HALF_UP)


def exceeds_change_limit(old: Decimal, new: Decimal, max_change_pct: Decimal) -> bool:
    """Whether `new` is strictly more than `max_change_pct` percent away from `old`, compared on the
    exact ratio (a true 15.004 percent is above 15, whatever it rounds to)."""
    return abs(new - old) * 100 > max_change_pct * old


def _usable_price(value: object) -> TypeIs[Decimal]:
    return isinstance(value, Decimal) and value.is_finite() and value > 0


def plan_price_actions(
    observations: Sequence[PriceObservation],
    products: Mapping[int, PriceProduct],
    *,
    now: datetime,
    max_age: timedelta,
    vat_rate: Decimal,
    max_change_pct: Decimal = DEFAULT_MAX_CHANGE_PCT,
) -> list[PriceAction]:
    """One action per observation. Stale data is flagged, never applied; a product on sale goes to
    review (its regular price is not written); a change strictly above `max_change_pct` percent goes
    to review as a price jump; anything else is applied as a net price (gross / (1 + VAT)).
    `vat_rate` and `max_age` are required: the caller derives the rate from `apps.tax` (product
    tax category, vendor tax position at the observation time)."""
    plan: list[PriceAction] = []
    for obs in observations:
        product = products.get(obs.product_id) if obs.product_id is not None else None
        pid = product.product_id if product else obs.product_id
        if product is None:
            plan.append(PriceAction(obs.ref, pid, SKIP, "unlinked"))
        elif is_stale(obs.observed_at, now=now, max_age=max_age):
            plan.append(PriceAction(obs.ref, pid, REVIEW, "stale", review_kind="stale"))
        elif not _usable_price(obs.price_gross):
            plan.append(PriceAction(obs.ref, pid, SKIP, "invalid_price"))
        elif obs.sale_price_gross is not None and not _usable_price(obs.sale_price_gross):
            plan.append(PriceAction(obs.ref, pid, SKIP, "invalid_sale_price"))
        else:
            new = net_from_gross(obs.price_gross, vat_rate=vat_rate)
            old = product.price_net
            if obs.sale_price_gross is not None:
                plan.append(PriceAction(obs.ref, pid, REVIEW, "sale", new, old, None, "sale"))
            elif old == new:
                plan.append(PriceAction(obs.ref, pid, UNCHANGED, "same", new, old))
            elif old is None or old <= 0:
                plan.append(PriceAction(obs.ref, pid, SET_PRICE, "first_price", new, old))
            else:
                pct = change_pct(old, new)
                if exceeds_change_limit(old, new, max_change_pct):
                    plan.append(
                        PriceAction(obs.ref, pid, REVIEW, "price_jump", new, old, pct, "price_jump")
                    )
                else:
                    plan.append(PriceAction(obs.ref, pid, SET_PRICE, "within_limit", new, old, pct))
    return plan


# --- Daily report (ING.10)

REVIEW_KINDS: Final = ("price_jump", "sale", "identity_change", "stale", "duplicate_sku")
IDENTITY_KINDS: Final = ("duplicate_sku", "identity_change")
REPORT_ERROR_CHARS: Final = 200
_COUNTERS: Final = ("seen", "created", "updated", "unchanged", "skipped", "errors")


@dataclass(frozen=True)
class ReportRun:
    """One import run of the day, as the report sees it."""

    source: str
    vendor: str
    kind: str
    status: str
    dry_run: bool
    seen: int
    updated: int
    unchanged: int
    skipped: int
    errors: int
    error_text: str = ""
    created: int = 0


@dataclass(frozen=True)
class ReportReview:
    """One review item: opened on the report day, still open, or both."""

    source: str
    vendor: str
    kind: str
    sku: str
    open: bool
    opened_today: bool
    blocking: bool


@dataclass(frozen=True)
class ReportCollision:
    """Several feed rows of one vendor share one SKU match key."""

    vendor: str
    sku_key: str
    items: int


@dataclass(frozen=True)
class Anomaly:
    """An identity anomaly: an ambiguous SKU, an identity change or a SKU used by several rows."""

    kind: str
    vendor: str
    sku: str
    detail: str = ""


@dataclass
class DailyReport:
    day: date
    runs: list[ReportRun]
    runs_total: int
    runs_failed: int
    totals: dict[str, int]
    error_runs: list[ReportRun]
    opened_by_kind: dict[str, int]
    open_total: int
    open_blocking: int
    stale_open: int
    missing_items: int
    anomalies: list[Anomaly]
    unavailable: list[str]
    needs_attention: bool = False


def build_daily_report(
    day: date,
    *,
    runs: Sequence[ReportRun],
    reviews: Sequence[ReportReview],
    collisions: Sequence[ReportCollision],
    missing_items: int,
    unavailable: Sequence[str],
) -> DailyReport:
    """The day's report from plain rows: run counters, errors, review items opened and still open,
    stale items and identity anomalies. Vendors are never merged: the same SKU at two vendors is
    two lines, and only rows of one vendor can collide."""
    totals = {name: sum(getattr(r, name) for r in runs) for name in _COUNTERS}
    error_runs = [
        ReportRun(**{**r.__dict__, "error_text": r.error_text[:REPORT_ERROR_CHARS]})
        for r in runs
        if r.status == FAILED or r.errors
    ]
    opened = {kind: 0 for kind in REVIEW_KINDS}
    for review in reviews:
        if review.opened_today and review.kind in opened:
            opened[review.kind] += 1
    anomalies = [
        Anomaly(r.kind, r.vendor, r.sku)
        for r in reviews
        if r.kind in IDENTITY_KINDS and (r.open or r.opened_today)
    ]
    anomalies += [
        Anomaly("sku_collision", c.vendor, c.sku_key, f"{c.items} rows") for c in collisions
    ]
    order = {"duplicate_sku": 0, "identity_change": 1, "sku_collision": 2}
    anomalies.sort(key=lambda a: (order[a.kind], a.vendor, a.sku))
    failed = sum(1 for r in runs if r.status == FAILED)
    open_reviews = [r for r in reviews if r.open]
    report = DailyReport(
        day=day,
        runs=list(runs),
        runs_total=len(runs),
        runs_failed=failed,
        totals=totals,
        error_runs=error_runs,
        opened_by_kind=opened,
        open_total=len(open_reviews),
        open_blocking=sum(1 for r in open_reviews if r.blocking),
        stale_open=sum(1 for r in open_reviews if r.kind == "stale"),
        missing_items=missing_items,
        anomalies=anomalies,
        unavailable=list(unavailable),
    )
    report.needs_attention = bool(failed or totals["errors"] or anomalies or unavailable)
    return report


def report_context(report: DailyReport) -> dict[str, Any]:
    """The report as plain JSON-ready values, for the email context and the admin page."""
    return {
        "day": report.day.isoformat(),
        "runs": [r.__dict__ for r in report.runs],
        "runs_total": report.runs_total,
        "runs_failed": report.runs_failed,
        "totals": report.totals,
        "error_runs": [r.__dict__ for r in report.error_runs],
        "opened_by_kind": report.opened_by_kind,
        "open_total": report.open_total,
        "open_blocking": report.open_blocking,
        "stale_open": report.stale_open,
        "missing_items": report.missing_items,
        "anomalies": [a.__dict__ for a in report.anomalies],
        "anomaly_count": len(report.anomalies),
        "unavailable": report.unavailable,
        "needs_attention": report.needs_attention,
    }
