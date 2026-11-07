"""CAT.1 and CAT.2: pure catalog rules (spec 03 §3.2): SKU normalization and price validation,
the rules of the category and term trees, and the term attributes a taxonomy declares.

Taxonomies are data: their terms' extra fields are declared by the taxonomy (`term_attributes`)
and validated here, without the database.
"""

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from apps.catalog import engine

JAN = dt.datetime(2027, 1, 1, tzinfo=dt.UTC)
FEB = dt.datetime(2027, 2, 1, tzinfo=dt.UTC)


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [("abc-123", "ABC-123"), ("  Ab_12 ", "AB_12"), ("ABC", "ABC"), ("x9", "X9")],
)
def test_sku_is_stripped_and_upper_cased(raw: str, normalized: str) -> None:
    assert engine.normalize_sku(raw) == normalized


@pytest.mark.parametrize("raw", ["", "   ", "\t\n"])
def test_an_empty_sku_is_refused(raw: str) -> None:
    with pytest.raises(engine.InvalidSku):
        engine.normalize_sku(raw)


@pytest.mark.parametrize(
    ("raw", "price"),
    [
        (Decimal("12.5"), Decimal("12.50")),
        ("19.99", Decimal("19.99")),
        (10, Decimal("10.00")),
        ("0", Decimal("0.00")),
    ],
)
def test_prices_are_exact_decimals_with_two_places(raw: object, price: Decimal) -> None:
    result = engine.to_price(raw)
    assert result == price
    assert result.as_tuple().exponent == -2


def test_float_prices_are_refused() -> None:
    with pytest.raises(TypeError):
        engine.to_price(12.5)


def test_prices_with_more_than_two_decimals_are_refused_not_rounded() -> None:
    with pytest.raises(ValueError, match="2 decimals"):
        engine.to_price("10.005")


def test_negative_prices_are_refused() -> None:
    with pytest.raises(ValueError, match="negative"):
        engine.to_price("-0.01")


def test_a_regular_price_without_a_sale_is_valid() -> None:
    assert engine.price_errors(Decimal("10.00"), None, None, None) == {}


def test_a_sale_price_below_the_regular_price_in_a_valid_period_is_valid() -> None:
    assert engine.price_errors(Decimal("10.00"), Decimal("8.00"), JAN, FEB) == {}


@pytest.mark.parametrize("sale", [Decimal("10.00"), Decimal("12.00")])
def test_a_sale_price_must_be_below_the_regular_price(sale: Decimal) -> None:
    errors = engine.price_errors(Decimal("10.00"), sale, None, None)
    assert set(errors) == {"sale_price_net"}


@pytest.mark.parametrize("end", [JAN, JAN - dt.timedelta(days=1)])
def test_a_sale_must_end_after_it_starts(end: dt.datetime) -> None:
    errors = engine.price_errors(Decimal("10.00"), Decimal("8.00"), JAN, end)
    assert set(errors) == {"sale_to"}


def test_a_sale_period_without_a_sale_price_is_refused() -> None:
    errors = engine.price_errors(Decimal("10.00"), None, JAN, FEB)
    assert set(errors) == {"sale_price_net"}


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


SCHEMA: list[dict[str, Any]] = [
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


@pytest.mark.parametrize(
    "schema", [{"code": "x"}, ["model_year"], [{"kind": "text"}], [{"code": "x", "kind": "date"}]]
)
def test_term_attributes_against_an_invalid_schema_are_refused(schema: object) -> None:
    assert engine.term_attribute_errors(schema, {"x": 1}) == [
        "The taxonomy declares its term attributes wrongly: correct the taxonomy first."
    ]
