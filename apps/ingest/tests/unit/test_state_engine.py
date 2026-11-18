"""ING.2: pure rules of source state, stock state and change history (no database)."""

from decimal import Decimal
from typing import Any

import pytest

from apps.ingest import engine

HOSTS = ("supplier.example", "www.supplier.example")


def child(native_id: int = 11, status: int | None = 1, quantity: int | None = 2) -> dict[str, Any]:
    return {
        "native_id": native_id,
        "sku": f"V-{native_id}",
        "stock_status": status,
        "stock_quantity": quantity,
    }


def variable(*children: dict[str, Any], native_id: int = 7) -> dict[str, Any]:
    kids = children or (child(11), child(12, 0, 0))
    return {
        "kind": "variable",
        "native_id": native_id,
        "sku": "BAG",
        "variations": {str(c["native_id"]): c for c in kids},
    }


def simple(status: int | None = 1, quantity: int | None = 3) -> dict[str, Any]:
    return {
        "kind": "simple",
        "native_id": 5,
        "sku": "BAG-S",
        "variations": {},
        "stock_status": status,
        "stock_quantity": quantity,
    }


@pytest.mark.parametrize(
    ("status", "quantity", "ok"),
    [
        (0, 0, True),
        (1, 1, True),
        (1, 40, True),
        (1, 0, False),
        (0, 2, False),
        (1, -1, False),
        (None, None, False),
        (True, 1, False),
        (1, True, False),
        ("1", 1, False),
        (1, 1.0, False),
    ],
)
def test_valid_stock_state_is_explicit_and_coherent(
    status: object, quantity: object, ok: bool
) -> None:
    assert engine.valid_stock_state(status, quantity) is ok


def test_require_stock_state_fails_closed() -> None:
    assert engine.require_stock_state(1, 4) == (1, 4)
    with pytest.raises(engine.StateError):
        engine.require_stock_state(1, 0)


def test_source_url_identity_is_normalised() -> None:
    url = engine.normalize_source_url(
        "http://www.supplier.example/product/bag", hosts=HOSTS, path_prefix="/product/"
    )
    assert url == "https://supplier.example/product/bag/"


@pytest.mark.parametrize(
    "bad",
    [
        "ftp://supplier.example/product/x",
        "https://other.example/product/x",
        "https://user:pw@supplier.example/product/x",
        "https://supplier.example:8443/product/x",
        "https://supplier.example/product/x?a=1",
        "https://supplier.example/shop/x",
    ],
)
def test_source_url_rejects_foreign_or_decorated_addresses(bad: str) -> None:
    with pytest.raises(engine.StateError):
        engine.normalize_source_url(bad, hosts=HOSTS, path_prefix="/product/")


def test_validate_variable_state_has_children_and_no_own_stock() -> None:
    value = engine.validate_state(variable())
    assert value["variations"]["11"]["present"] is True
    assert value.get("stock_status") is None


def test_validate_keeps_decimals_exact() -> None:
    state = simple()
    state["price"] = Decimal("160.10")
    assert engine.validate_state(state)["price"] == "160.10"


@pytest.mark.parametrize(
    "bad",
    [
        {**variable(), "stock_status": 1, "stock_quantity": 1},
        {**variable(), "native_id": None},
        {**variable(), "variations": {}},
        {**simple(), "variations": variable()["variations"]},
        simple(1, 0),
        {**simple(), "sku": "N/D"},
        {**simple(), "kind": "bundle"},
        {**simple(), "native_id": 0},
        variable(child(11), child(12, 1, 0)),
    ],
)
def test_validate_rejects_incomplete_or_incoherent_state(bad: dict[str, Any]) -> None:
    with pytest.raises(engine.StateError):
        engine.validate_state(bad)


def test_validate_rejects_a_child_key_that_is_not_its_id() -> None:
    bad = variable()
    bad["variations"]["99"] = bad["variations"].pop("11")
    with pytest.raises(engine.StateError):
        engine.validate_state(bad)


def test_unknown_child_stock_is_allowed_and_stays_unknown() -> None:
    value = engine.validate_state(variable(child(11, None, None)))
    assert value["variations"]["11"]["stock_status"] is None


def test_merge_keeps_unlisted_children_as_absent_with_last_data() -> None:
    old = engine.merge_state(None, engine.validate_state(variable()))
    new = engine.validate_state(variable(child(11, 1, 9)))
    merged = engine.merge_state(old, new)
    assert merged["variations"]["11"]["stock_quantity"] == 9
    assert merged["variations"]["12"]["present"] is False
    assert merged["variations"]["12"]["stock_quantity"] == 0


def test_merge_unions_source_urls_and_refuses_a_changed_native_id() -> None:
    first = engine.merge_state(None, {**engine.validate_state(variable()), "source_urls": ["a"]})
    second = engine.merge_state(first, {**engine.validate_state(variable()), "source_urls": ["b"]})
    assert second["source_urls"] == ["a", "b"]
    with pytest.raises(engine.StateError):
        engine.merge_state(first, engine.validate_state(variable(native_id=8)))


def test_changed_fields_lists_only_differences() -> None:
    assert engine.changed_fields({"a": 1, "b": 2}, {"a": 1, "b": 3, "c": 4}) == {"b": 3, "c": 4}
    assert engine.changed_fields(None, {"a": 1}) == {"a": 1}


def test_latest_field_values_takes_last_flagged_value_in_order() -> None:
    rows = [
        ({"price": True, "title": True}, {"price": "1", "title": "A"}),
        ({"price": True}, {"price": "2", "title": "ignored"}),
        ({"title": True}, {"title": "B", "price": "ignored"}),
    ]
    assert engine.latest_field_values(rows) == {"price": "2", "title": "B"}
    assert engine.latest_field_values(rows, ["price"]) == {"price": "2"}
    assert engine.latest_field_values([]) == {}
