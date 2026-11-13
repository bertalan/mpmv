"""FIT.1: the pure rules of the scheme and the nodes (no database)."""

import pytest

from apps.fitment import engine


def test_names_are_compared_after_nfc_case_and_punctuation_folding() -> None:
    composed = "Citroën"
    decomposed = "Citroën"
    assert engine.normalize_name(composed) == engine.normalize_name(decomposed)
    assert engine.normalize_name("  Model-X  ") == engine.normalize_name("model x")
    assert engine.normalize_name("MODEL   X") == "model x"


def test_a_name_with_no_letter_or_digit_normalises_to_nothing() -> None:
    assert engine.normalize_name(" -- ") == ""


def test_the_natural_sort_key_puts_model_9_before_model_10() -> None:
    names = ["Model 10", "Model 9", "Model 100", "Model 2"]
    assert sorted(names, key=engine.natural_sort_key) == [
        "Model 2",
        "Model 9",
        "Model 10",
        "Model 100",
    ]


def test_a_scheme_has_at_most_four_node_levels() -> None:
    assert engine.add_level_error([1, 2, 3]) is None
    assert engine.add_level_error([1, 2, 3, 4]) is not None


def test_a_level_can_only_be_removed_from_the_end_and_when_empty() -> None:
    codes = ["make", "model", "variant"]
    assert engine.remove_level_error(codes, "variant", {}) is None
    assert engine.remove_level_error(codes, "model", {}) is not None
    assert engine.remove_level_error(codes, "variant", {"variant": 3}) is not None
    assert engine.remove_level_error(codes, "missing", {}) is not None


def test_levels_can_be_reordered_only_while_no_moved_level_has_nodes() -> None:
    current = ["make", "model", "variant"]
    swapped = ["model", "make", "variant"]
    assert engine.reorder_error(current, swapped, {}) is None
    assert engine.reorder_error(current, swapped, {"variant": 5}) is None  # not moved
    assert engine.reorder_error(current, swapped, {"make": 1}) is not None
    assert engine.reorder_error(current, current, {"make": 1}) is None
    assert engine.reorder_error(current, ["make", "model"], {}) is not None  # not a permutation


def test_a_node_may_only_be_merged_into_another_node_of_the_same_level() -> None:
    assert engine.merge_error("0001", "0002", 1, 1) is None
    assert engine.merge_error("0001", "0001", 1, 1) is not None  # itself
    assert engine.merge_error("0001", "0002", 1, 2) is not None  # other level


def test_a_node_is_never_merged_into_its_own_subtree() -> None:
    assert engine.merge_error("0001", "00010001", 1, 1) is not None


@pytest.mark.parametrize(
    ("old", "new", "allowed"),
    [
        ("proposed", "active", True),
        ("active", "retired", True),
        ("retired", "active", True),
        ("active", "proposed", False),
        ("retired", "proposed", False),
        ("proposed", "retired", False),
        ("active", "active", False),
    ],
)
def test_status_transitions(old: str, new: str, allowed: bool) -> None:
    assert (engine.status_transition_error(old, new) is None) is allowed


def test_clashing_names_are_found_after_normalisation() -> None:
    siblings = {"Model A": 1, "Model-B": 2}
    assert engine.name_clash("model a", siblings) == 1
    assert engine.name_clash("Model B", siblings) == 2
    assert engine.name_clash("Model C", siblings) is None
