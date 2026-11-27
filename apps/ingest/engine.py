"""Pure rules of the ingest app (ING.1): run status machine, review decisions, SKU matching and
mapping lookup. No ORM, no Django: the services load the rows and call these functions."""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Final, Literal
from urllib.parse import urlsplit, urlunsplit

from apps.catalog.engine import (
    ProductIn,
    TranslationIn,
    publication_errors,
    sku_match_key,
    to_price,
)

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


# --- Publishing drafts (ING.7)


@dataclass(frozen=True)
class DraftProblem:
    """Why a draft cannot be turned into a catalog payload."""

    reason: str


def is_publishable_status(status: str) -> bool:
    """Only a draft marked `ready` is published; a person or the pipeline sets it."""
    return status == "ready"


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ValueError(str(value)) from None
    if not number.is_finite():
        raise ValueError(str(value))
    return number


def _whole(value: object) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise ValueError(str(value))
    return int(value)


def plan_draft_product(
    sku: str,
    extracted: Mapping[str, Any],
    enriched: Mapping[str, Any],
    *,
    language: str,
) -> ProductIn | DraftProblem:
    """The catalog payload of a draft, with the text in `language` only (D-18 bis, Q-71).

    Other languages in `enriched` are ignored: a new draft publishes in the primary language
    only, and an existing product keeps the texts it already has because the catalog upsert
    leaves languages the payload omits alone. The enriched text wins over the extracted one.
    Pure: no ORM. Returns a `DraftProblem` for a draft that would not pass the catalog rules.
    """
    written = enriched.get(language)
    text = written if isinstance(written, Mapping) else {}

    def pick(field: str) -> str:
        return _text(text.get(field)) or _text(extracted.get(field))

    title = pick("title")
    if not title:
        return DraftProblem("The draft has no title.")
    try:
        price = _decimal(extracted.get("price"))
        weight = _decimal(extracted.get("weight_kg"))
        sizes = [_whole(extracted.get(f"{d}_mm")) for d in ("length", "width", "height")]
    except ValueError as exc:
        return DraftProblem(f"The draft has a malformed number: {exc}.")
    if price is None or price < 0:
        return DraftProblem("The draft has no valid price.")
    payload = ProductIn(
        sku=sku,
        price_net=to_price(price),
        weight_kg=weight,
        length_mm=sizes[0],
        width_mm=sizes[1],
        height_mm=sizes[2],
        country_of_origin=_text(extracted.get("country_of_origin")),
        translations=(
            TranslationIn(
                language=language,
                title=title,
                short_description=pick("short_description"),
                description=pick("description"),
                material=pick("material"),
                colour=pick("colour"),
            ),
        ),
    )
    problems = publication_errors(
        payload.sales_mode, payload.price_net, payload.weight_kg, [language], language
    )
    if problems:
        return DraftProblem(" ".join(problems))
    return payload
