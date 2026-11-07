"""ChangeSet: the diff every mutating service returns, applied or not (dry-run)."""

import datetime as dt
import uuid
from decimal import Decimal

from apps.core.changeset import Change, ChangeSet, diff


def test_diff_lists_only_changed_fields() -> None:
    before = {"price": Decimal("10.00"), "stock": 3, "title": "A"}
    after = {"price": Decimal("12.00"), "stock": 3, "title": "A"}
    assert diff(before, after) == {"price": (Decimal("10.00"), Decimal("12.00"))}


def test_diff_handles_added_and_removed_keys() -> None:
    assert diff({"a": 1}, {"b": 2}) == {"a": (1, None), "b": (None, 2)}


def test_empty_changeset() -> None:
    cs = ChangeSet()
    assert cs.is_empty
    assert cs.summary() == {"create": 0, "update": 0, "delete": 0}


def test_changeset_collects_changes_and_summarises() -> None:
    cs = ChangeSet()
    cs.create("catalog.product", "A-1", {"price": Decimal("10.00")})
    cs.update("catalog.product", "A-2", {"price": Decimal("10.00")}, {"price": Decimal("11.00")})
    cs.update("catalog.product", "A-3", {"price": Decimal("5.00")}, {"price": Decimal("5.00")})
    cs.delete("catalog.product", "A-4", {"price": Decimal("1.00")})
    # The no-op update on A-3 is dropped: a ChangeSet only lists real changes.
    assert [c.key for c in cs.changes] == ["A-1", "A-2", "A-4"]
    assert cs.summary() == {"create": 1, "update": 1, "delete": 1}
    assert not cs.is_empty


def test_change_fields_reports_what_changed() -> None:
    change = Change("update", "catalog.product", "A-2", {"a": 1, "b": 2}, {"a": 1, "b": 3})
    assert change.fields == {"b": (2, 3)}


def test_to_dict_is_json_ready() -> None:
    cs = ChangeSet()
    uid = uuid.UUID("12345678-1234-5678-1234-567812345678")
    cs.update(
        "orders.order",
        "1",
        {"total": Decimal("1.50"), "at": dt.date(2026, 11, 7)},
        {"total": Decimal("2.50"), "at": dt.date(2026, 11, 8), "ref": uid},
    )
    assert cs.to_dict() == {
        "summary": {"create": 0, "update": 1, "delete": 0},
        "changes": [
            {
                "op": "update",
                "model": "orders.order",
                "key": "1",
                "fields": {
                    "total": ["1.50", "2.50"],
                    "at": ["2026-11-07", "2026-11-08"],
                    "ref": [None, "12345678-1234-5678-1234-567812345678"],
                },
            }
        ],
    }
