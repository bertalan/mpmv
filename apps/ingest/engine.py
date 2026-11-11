"""Pure rules of the ingest app (ING.1): run status machine, review decisions, SKU matching and
mapping lookup. No ORM, no Django: the services load the rows and call these functions."""

import calendar
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final, Literal

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


# --- Vendor migration choices (MIG.16, owner decision D-16, 2026-11-11)

PERIOD_CHOICES: Final = (12, 24, 36)  # months of orders kept active; None means "all"
LOGIN_WINDOWS: Final = (1, 2, 3, 6, 12, 24, 36)  # months, owner 2026-11-11 (L-11)
DATA_ORDERS: Final = "orders"
DATA_CUSTOMERS: Final = "customers"  # the customers of the orders in scope
DATA_PRODUCTS: Final = "products"
DATA_REVIEWS: Final = "reviews"
DATA_TYPES: Final = (DATA_ORDERS, DATA_CUSTOMERS, DATA_PRODUCTS, DATA_REVIEWS)
DEADLINE_DAYS: Final = 14  # the choice is due this many days before the cutover
REMINDER_DAYS_BEFORE_DEADLINE: Final = (14, 7, 1)
CHOICE_SOURCES: Final = ("vendor", "staff", "default")
SOURCE_VENDOR, SOURCE_STAFF, SOURCE_DEFAULT = CHOICE_SOURCES


@dataclass(frozen=True)
class ChoiceValues:
    """What a vendor chose to migrate. `period_months` None means all orders."""

    period_months: int | None
    data_types: tuple[str, ...]
    include_unpublished: bool  # drafts and archived products come along as hidden products
    login_window_months: int


DEFAULT_CHOICE: Final = ChoiceValues(
    period_months=24, data_types=DATA_TYPES, include_unpublished=False, login_window_months=12
)


def normalise_data_types(chosen: Sequence[str]) -> tuple[str, ...]:
    """Unique values in canonical order; unknown ones come last so validation sees them."""
    known = tuple(t for t in DATA_TYPES if t in chosen)
    return known + tuple(sorted({t for t in chosen if t not in DATA_TYPES}))


def validate_choice(values: ChoiceValues) -> dict[str, str]:
    """Field -> message; empty when the choice is valid. Nothing outside the lists is accepted."""
    errors: dict[str, str] = {}
    if values.period_months is not None and values.period_months not in PERIOD_CHOICES:
        errors["period_months"] = "Choose 12, 24 or 36 months, or all."
    if values.login_window_months not in LOGIN_WINDOWS:
        errors["login_window_months"] = "Choose 1, 2, 3, 6, 12, 24 or 36 months."
    unknown = [t for t in values.data_types if t not in DATA_TYPES]
    if unknown:
        errors["data_types"] = "Unknown data type."
    elif DATA_CUSTOMERS in values.data_types and DATA_ORDERS not in values.data_types:
        errors["data_types"] = "Customers are migrated with their orders: choose orders too."
    return errors


def choice_snapshot(values: ChoiceValues) -> dict[str, Any]:
    return {
        "period_months": values.period_months,
        "data_types": list(values.data_types),
        "include_unpublished": values.include_unpublished,
        "login_window_months": values.login_window_months,
    }


def choice_deadline(cutover: datetime) -> datetime:
    """The moment the choice is due: 14 days before the cutover (a `datetime`)."""
    return cutover - timedelta(days=DEADLINE_DAYS)


def due_reminder(now: datetime, deadline: datetime) -> int | None:
    """The reminder (days before the deadline) to send at `now`, or None.

    The closest offset already reached wins, so a late start sends one reminder, not three.
    Nothing is due once the deadline has passed.
    """
    if now >= deadline:
        return None
    reached = [d for d in REMINDER_DAYS_BEFORE_DEADLINE if deadline - timedelta(days=d) <= now]
    return min(reached) if reached else None


def months_before(moment: datetime, months: int) -> datetime:
    """`moment` shifted back by whole calendar months, the day clamped to the month's length."""
    index = moment.year * 12 + (moment.month - 1) - months
    year, month = divmod(index, 12)
    day = min(moment.day, calendar.monthrange(year, month + 1)[1])
    return moment.replace(year=year, month=month + 1, day=day)
