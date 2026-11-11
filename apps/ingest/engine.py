"""Pure rules of the ingest app (ING.1): run status machine, review decisions, SKU matching and
mapping lookup. No ORM, no Django: the services load the rows and call these functions."""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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
