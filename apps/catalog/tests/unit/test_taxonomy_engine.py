"""CAT.2: pure rules of the category tree and of the configurable taxonomies (spec 03 §3.2).

Taxonomies are data: their terms' extra fields are declared by the taxonomy (`term_attributes`)
and validated here, without the database.
"""

import pytest

from apps.catalog import engine

# --- Tree paths (treebeard materialized path, 4 characters per level)


def test_ancestor_paths_of_a_root_is_empty() -> None:
    assert engine.ancestor_paths("0001", steplen=4) == []


def test_ancestor_paths_lists_every_upper_level_root_first() -> None:
    assert engine.ancestor_paths("000100020003", steplen=4) == ["0001", "00010002"]


def test_ancestor_paths_rejects_a_malformed_path() -> None:
    with pytest.raises(ValueError, match="path"):
        engine.ancestor_paths("00010", steplen=4)


# --- Term placement


def test_a_root_term_is_always_placed() -> None:
    assert engine.term_placement_error(hierarchical=False, taxonomy=1, parent_taxonomy=None) is None


def test_a_child_term_of_the_same_hierarchical_taxonomy_is_placed() -> None:
    assert engine.term_placement_error(hierarchical=True, taxonomy=1, parent_taxonomy=1) is None


def test_a_flat_taxonomy_has_no_child_terms() -> None:
    error = engine.term_placement_error(hierarchical=False, taxonomy=1, parent_taxonomy=1)
    assert error == "This taxonomy is flat: its terms cannot have children."


def test_a_term_cannot_be_placed_under_a_term_of_another_taxonomy() -> None:
    error = engine.term_placement_error(hierarchical=True, taxonomy=1, parent_taxonomy=2)
    assert error == "A term can only be placed under a term of the same taxonomy."


# --- Term attribute schema (declared by the taxonomy)


def test_an_empty_schema_is_valid() -> None:
    assert engine.term_schema_errors([]) == []


def test_a_schema_lists_typed_attributes() -> None:
    schema = [
        {"code": "model_year", "kind": "number", "required": True, "label": "Model year"},
        {"code": "maker", "kind": "text"},
        {"code": "discontinued", "kind": "boolean", "required": False},
    ]
    assert engine.term_schema_errors(schema) == []


@pytest.mark.parametrize(
    ("schema", "message"),
    [
        ({"code": "x"}, "The term attributes must be a list of attribute definitions."),
        (["x"], "Attribute 1 must be an object with a code and a kind."),
        ([{"kind": "text"}], "Attribute 1 needs a code: lower case letters, digits and _."),
        ([{"code": "Model Year", "kind": "text"}], "Attribute 1 needs a code: lower case"),
        ([{"code": "x", "kind": "date"}], "Attribute x: the kind must be one of text, number"),
        ([{"code": "x", "kind": "text", "required": "yes"}], "Attribute x: required must be"),
        ([{"code": "x", "kind": "text", "unit": "cm"}], "Attribute x: unknown key unit."),
        ([{"code": "x", "kind": "text"}, {"code": "x", "kind": "number"}], "twice: x."),
    ],
)
def test_malformed_schemas_are_rejected(schema: object, message: str) -> None:
    errors = engine.term_schema_errors(schema)
    assert any(message in error for error in errors), errors


SCHEMA = [
    {"code": "model_year", "kind": "number", "required": True},
    {"code": "maker", "kind": "text"},
    {"code": "discontinued", "kind": "boolean"},
]


def test_term_attributes_matching_the_schema_are_valid() -> None:
    values = {"model_year": 2020, "maker": "Maker A", "discontinued": False}
    assert engine.term_attribute_errors(SCHEMA, values) == []


def test_optional_attributes_may_be_left_out() -> None:
    assert engine.term_attribute_errors(SCHEMA, {"model_year": 2020}) == []


def test_a_required_attribute_must_be_given() -> None:
    assert engine.term_attribute_errors(SCHEMA, {}) == ["model_year is required."]


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"model_year": "2020"}, "model_year must be a number."),
        ({"model_year": True}, "model_year must be a number."),
        ({"model_year": 2020, "maker": 7}, "maker must be a text."),
        ({"model_year": 2020, "discontinued": "no"}, "discontinued must be true or false."),
        ({"model_year": 2020, "colour": "red"}, "colour is not an attribute of this taxonomy."),
    ],
)
def test_term_attributes_of_the_wrong_kind_are_rejected(values: object, message: str) -> None:
    assert engine.term_attribute_errors(SCHEMA, values) == [message]


def test_term_attributes_must_be_an_object() -> None:
    assert engine.term_attribute_errors(SCHEMA, ["model_year"]) == [
        "The attributes must be an object of attribute codes and values."
    ]
