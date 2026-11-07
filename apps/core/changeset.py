"""ChangeSet: the diff returned by every mutating service, applied or not (dry-run).

Pure Python, no ORM: services build a ChangeSet, then either return it (dry-run) or apply it
and audit it (see `apps.core.services.run_mutation`).
"""

import datetime as dt
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

Op = Literal["create", "update", "delete"]
Snapshot = Mapping[str, Any]


def diff(before: Snapshot, after: Snapshot) -> dict[str, tuple[Any, Any]]:
    """Fields whose value differs, as {field: (before, after)}; missing keys count as None."""
    keys = [*before, *(k for k in after if k not in before)]
    return {k: (before.get(k), after.get(k)) for k in keys if before.get(k) != after.get(k)}


def to_jsonable(value: Any) -> Any:
    """Make audit and API payloads JSON-safe without losing exactness (Decimal → str)."""
    if isinstance(value, Decimal | uuid.UUID):
        return str(value)
    if isinstance(value, dt.date | dt.datetime | dt.time):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [to_jsonable(v) for v in value]
    return value


@dataclass(frozen=True)
class Change:
    op: Op
    model: str  # "app_label.model"
    key: str  # natural or public key, human-readable in reports
    before: Snapshot | None
    after: Snapshot | None

    @property
    def fields(self) -> dict[str, tuple[Any, Any]]:
        return diff(self.before or {}, self.after or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": self.op,
            "model": self.model,
            "key": self.key,
            "fields": {k: [to_jsonable(a), to_jsonable(b)] for k, (a, b) in self.fields.items()},
        }


@dataclass
class ChangeSet:
    changes: list[Change] = field(default_factory=list)

    def create(
        self, model: str, key: str, after: Snapshot, *, supersedes: Snapshot | None = None
    ) -> None:
        """A new row. For versioned configuration (operator, rates), `supersedes` is the
        version it replaces: it becomes the change's `before`, so the audit shows the diff."""
        before = None if supersedes is None else dict(supersedes)
        self.changes.append(Change("create", model, key, before, dict(after)))

    def update(self, model: str, key: str, before: Snapshot, after: Snapshot) -> None:
        change = Change("update", model, key, dict(before), dict(after))
        if change.fields:  # no-op updates are not changes
            self.changes.append(change)

    def delete(self, model: str, key: str, before: Snapshot) -> None:
        self.changes.append(Change("delete", model, key, dict(before), None))

    @property
    def is_empty(self) -> bool:
        return not self.changes

    def summary(self) -> dict[str, int]:
        counts = {"create": 0, "update": 0, "delete": 0}
        for change in self.changes:
            counts[change.op] += 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {"summary": self.summary(), "changes": [c.to_dict() for c in self.changes]}
