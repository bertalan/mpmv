"""ING.1: the pure rules of the ingest app (status machine, SKU matching, mapping precedence)."""

import pytest

from apps.ingest import engine
from apps.ingest.engine import MappingEntry


def entry(
    key: str, *, vendor_id: int | None = None, aliases: tuple[str, ...] = (), kind: str = "color"
) -> MappingEntry:
    return MappingEntry(
        vendor_id=vendor_id, kind=kind, key=key, aliases=aliases, target_type="", target_id=None,
        target_value=f"to-{key}",
    )  # fmt: skip


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("queued", "running"),
        ("queued", "cancelled"),
        ("running", "succeeded"),
        ("running", "failed"),
        ("running", "cancelled"),
    ],
)
def test_allowed_run_transitions(source: str, target: str) -> None:
    assert engine.can_transition_run(source, target)


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("queued", "succeeded"),
        ("queued", "failed"),
        ("running", "queued"),
        ("succeeded", "running"),
        ("failed", "running"),
        ("cancelled", "queued"),
        ("succeeded", "succeeded"),
    ],
)
def test_forbidden_run_transitions(source: str, target: str) -> None:
    assert not engine.can_transition_run(source, target)


def test_terminal_run_statuses() -> None:
    assert {s for s in engine.RUN_STATUSES if engine.is_terminal_run(s)} == {
        "succeeded", "failed", "cancelled",
    }  # fmt: skip


def test_review_decisions_are_final_except_deferred() -> None:
    assert engine.can_decide("pending", "accepted")
    assert engine.can_decide("pending", "deferred")
    assert engine.can_decide("deferred", "rejected")
    assert not engine.can_decide("accepted", "rejected")
    assert not engine.can_decide("rejected", "pending")
    assert not engine.can_decide("deferred", "pending")


def test_ambiguity_and_identity_review_kinds_block() -> None:
    assert engine.is_blocking("duplicate_sku")
    assert engine.is_blocking("identity_change")
    assert not engine.is_blocking("price_jump")
    assert not engine.is_blocking("stale")


def test_sku_match_exact_alias_new_and_ambiguous() -> None:
    by_key = {"ab-1": [1], "dup": [2, 3]}
    by_alias = {"old-1": [4], "amb": [5, 6], "ab-1": [1], "swap": [8]}
    by_key["swap"] = [9]  # SKU-AMBIGUOUS: an old alias of one item is the live SKU of another
    assert engine.match_sku("ab-1", by_key=by_key, by_alias=by_alias) == engine.SkuMatch(
        "exact", (1,)
    )
    swapped = engine.match_sku("swap", by_key=by_key, by_alias=by_alias)
    assert (swapped.kind, set(swapped.product_ids)) == ("ambiguous", {8, 9})
    assert engine.match_sku("dup", by_key=by_key, by_alias=by_alias).kind == "ambiguous"
    assert engine.match_sku("old-1", by_key=by_key, by_alias=by_alias) == engine.SkuMatch(
        "alias", (4,)
    )
    assert engine.match_sku("amb", by_key=by_key, by_alias=by_alias).kind == "ambiguous"
    assert engine.match_sku("zzz", by_key=by_key, by_alias=by_alias) == engine.SkuMatch("new", ())


def test_an_alias_naming_the_same_product_twice_is_not_ambiguous() -> None:
    assert engine.match_sku("x", by_key={}, by_alias={"x": [7, 7]}) == engine.SkuMatch(
        "alias", (7,)
    )


def test_mapping_precedence_vendor_key_vendor_alias_global_key_global_alias() -> None:
    entries = [
        entry("red", vendor_id=None, aliases=("rosso",)),
        entry("rosso", vendor_id=None),
        entry("scarlet", vendor_id=1, aliases=("rosso",)),
        entry("rosso", vendor_id=1),
    ]
    found = engine.resolve_mapping(entries, vendor_id=1, kind="color", raw="Rosso")
    assert found is not None
    assert (found.vendor_id, found.key) == (1, "rosso")  # vendor key beats vendor alias
    only_alias = engine.resolve_mapping(entries[:3], vendor_id=1, kind="color", raw="rosso")
    assert only_alias is not None
    assert only_alias.key == "scarlet"  # vendor alias beats global key
    other_vendor = engine.resolve_mapping(entries, vendor_id=2, kind="color", raw="rosso")
    assert other_vendor is not None
    assert other_vendor.vendor_id is None
    assert other_vendor.key == "rosso"  # global key beats global alias
    assert engine.resolve_mapping(entries, vendor_id=2, kind="color", raw="red") == entries[0]


def test_mapping_ignores_other_kinds_and_other_vendors_and_normalises_the_key() -> None:
    entries = [entry("blue", vendor_id=1, kind="material"), entry("  Blue  ", vendor_id=3)]
    assert engine.resolve_mapping(entries, vendor_id=1, kind="color", raw="blue") is None
    assert engine.resolve_mapping(entries, vendor_id=1, kind="material", raw="BLUE") is not None
    assert engine.resolve_mapping(entries, vendor_id=1, kind="color", raw=" blue ") is None
    assert engine.resolve_mapping(entries, vendor_id=3, kind="color", raw="BLUE") is not None


def test_two_vendor_entries_with_the_same_key_are_a_conflict() -> None:
    entries = [entry("x", vendor_id=1), entry("X", vendor_id=1)]
    with pytest.raises(engine.AmbiguousMapping):
        engine.resolve_mapping(entries, vendor_id=1, kind="color", raw="x")


def test_payload_hash_is_stable_across_key_order() -> None:
    assert engine.payload_hash({"a": 1, "b": [1, 2]}) == engine.payload_hash({"b": [1, 2], "a": 1})
    assert engine.payload_hash({"a": 1}) != engine.payload_hash({"a": 2})
