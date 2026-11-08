"""CAT.1 and CAT.2: pure catalog rules (spec 03 §3.2): price validation,
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


# --- Typed attributes and variant axes (CAT.3, spec 03 §3.2, Q-32)

SIZE_COLOR = {
    "size": engine.AttributeSpec("choice", frozenset({"s", "m", "l"})),
    "color": engine.AttributeSpec("choice", frozenset({"red", "blue"})),
    "material": engine.AttributeSpec("choice", frozenset({"cotton", "linen"})),
    "bag_width": engine.AttributeSpec("number"),
    "author": engine.AttributeSpec("text"),
}
AXES = (
    engine.AttributeIn("size", values=("s", "m"), axis=True),
    engine.AttributeIn("color", values=("red", "blue"), axis=True),
)


def _variant(sku: str, **options: str) -> engine.VariantIn:
    return engine.VariantIn(sku=sku, options=options)


def _grid() -> tuple[engine.VariantIn, ...]:
    return (
        _variant("T-S-RED", size="s", color="red"),
        _variant("T-S-BLUE", size="s", color="blue"),
        _variant("T-M-RED", size="m", color="red"),
        _variant("T-M-BLUE", size="m", color="blue"),
    )


def test_a_size_by_color_variable_product_is_valid() -> None:
    attributes = (
        *AXES,
        engine.AttributeIn("material", values=("cotton",)),
        engine.AttributeIn("bag_width", number=Decimal("30.5")),
        engine.AttributeIn("author", text="A. Writer"),
    )
    assert engine.product_structure_errors("variable", SIZE_COLOR, attributes, _grid()) == []


def test_a_simple_product_with_plain_attributes_is_valid() -> None:
    attributes = (engine.AttributeIn("material", values=("cotton", "linen")),)
    assert engine.product_structure_errors("simple", SIZE_COLOR, attributes, ()) == []


@pytest.mark.parametrize(
    ("attribute", "message"),
    [
        (engine.AttributeIn("size"), "size needs at least one value."),
        (
            engine.AttributeIn("size", values=("s",), number=Decimal(1)),
            "size takes values, not a number or a text.",
        ),
        (engine.AttributeIn("size", values=("xxl",)), "size has no value xxl."),
        (engine.AttributeIn("size", values=("s", "s")), "size lists the value s twice."),
        (engine.AttributeIn("bag_width"), "bag_width needs a number."),
        (
            engine.AttributeIn("bag_width", number=Decimal(3), values=("s",)),
            "bag_width takes a number only.",
        ),
        (engine.AttributeIn("author"), "author needs a text."),
        (engine.AttributeIn("author", text="x", number=Decimal(1)), "author takes a text only."),
        (engine.AttributeIn("weight", values=("x",)), "weight is not a known attribute."),
    ],
)
def test_attribute_values_must_match_the_attribute_kind(
    attribute: engine.AttributeIn, message: str
) -> None:
    assert engine.product_structure_errors("simple", SIZE_COLOR, (attribute,), ()) == [message]


def test_an_attribute_is_given_once() -> None:
    attributes = (
        engine.AttributeIn("material", values=("cotton",)),
        engine.AttributeIn("material", values=("linen",)),
    )
    assert engine.product_structure_errors("simple", SIZE_COLOR, attributes, ()) == [
        "material is given twice."
    ]


def test_a_float_is_never_a_number_value() -> None:
    attribute = engine.AttributeIn("bag_width", number=30.5)  # type: ignore[arg-type]
    assert engine.product_structure_errors("simple", SIZE_COLOR, (attribute,), ()) == [
        "bag_width needs an exact decimal number."
    ]


def test_only_choice_attributes_can_be_variant_axes() -> None:
    attributes = (*AXES, engine.AttributeIn("author", text="x", axis=True))
    assert engine.product_structure_errors("variable", SIZE_COLOR, attributes, _grid()) == [
        "author cannot be a variant axis: only choice attributes can."
    ]


def test_a_simple_product_has_no_variants_and_no_axes() -> None:
    # Owner decision Q-32: variants exist only on variable products.
    errors = engine.product_structure_errors("simple", SIZE_COLOR, AXES, _grid())
    assert errors == [
        "Only a variable product has variant axes.",
        "Only a variable product has variants.",
    ]


def test_a_variable_product_needs_axes_and_variants() -> None:
    assert engine.product_structure_errors("variable", SIZE_COLOR, (), ()) == [
        "A variable product needs at least one variant axis.",
        "A variable product needs at least one variant.",
    ]


@pytest.mark.parametrize(
    ("variant", "message"),
    [
        (_variant("T-L", size="s"), "Variant T-L has no color."),
        (
            _variant("T-L", size="m", color="blue", material="cotton"),
            "Variant T-L: material is not a variant axis of this product.",
        ),
        (
            _variant("T-L", size="l", color="red"),
            "Variant T-L: l is not one of the product's size values.",
        ),
    ],
)
def test_a_variant_takes_one_listed_value_per_axis(variant: engine.VariantIn, message: str) -> None:
    variants = (*_grid()[:3], variant)
    assert engine.product_structure_errors("variable", SIZE_COLOR, AXES, variants) == [message]


def test_two_variants_cannot_share_a_combination_or_a_sku() -> None:
    variants = (
        *_grid(),
        _variant("T-OTHER", size="s", color="red"),
        _variant("t-s-red ", size="m", color="blue"),
    )
    errors = engine.product_structure_errors("variable", SIZE_COLOR, AXES, variants)
    assert "Variants T-S-RED and T-OTHER have the same options." in errors
    assert "The variant SKU T-S-RED is given twice." in errors


def test_variant_skus_given_twice_compare_by_match_key() -> None:
    """CAT.13: two variant SKUs that differ only in case and spaces are the same SKU."""
    variants = (
        _variant("T-S  RED", size="s", color="red"),
        _variant("t-s red", size="s", color="blue"),
        *_grid()[2:],
    )
    errors = engine.product_structure_errors("variable", SIZE_COLOR, AXES, variants)
    assert errors == ["The variant SKU T-S  RED is given twice."]


def test_at_most_one_default_variant() -> None:
    first, second, *rest = _grid()
    variants = (
        engine.VariantIn(sku=first.sku, options=first.options, is_default=True),
        engine.VariantIn(sku=second.sku, options=second.options, is_default=True),
        *rest,
    )
    assert engine.product_structure_errors("variable", SIZE_COLOR, AXES, variants) == [
        "Only one variant can be the default."
    ]


def test_variants_are_ordered_by_axis_order_then_value_position() -> None:
    positions = {"size": {"s": 0, "m": 1, "l": 2}, "color": {"red": 0, "blue": 1}}
    options = [
        {"size": "m", "color": "blue"},
        {"size": "s", "color": "blue"},
        {"size": "m", "color": "red"},
        {"size": "s", "color": "red"},
    ]
    assert engine.variant_order(("size", "color"), positions, options) == [3, 1, 2, 0]
    # Color first: the axis order of the product decides.
    assert engine.variant_order(("color", "size"), positions, options) == [3, 2, 1, 0]


def test_a_variant_label_joins_the_option_labels_in_axis_order() -> None:
    assert engine.variant_label(["M", "Rosso"]) == "M / Rosso"
    assert engine.variant_label([]) == ""


@pytest.mark.parametrize(
    ("number", "message"),
    [
        (Decimal("30.5555"), "bag_width has more than 3 decimals."),
        (Decimal("1000000000"), "bag_width has more than 9 digits before the decimal point."),
        (Decimal("-1000000000"), "bag_width has more than 9 digits before the decimal point."),
        (Decimal("1E+40"), "bag_width has more than 9 digits before the decimal point."),
    ],
)
def test_a_number_value_must_fit_the_stored_precision(number: Decimal, message: str) -> None:
    attribute = engine.AttributeIn("bag_width", number=number)
    assert engine.product_structure_errors("simple", SIZE_COLOR, (attribute,), ()) == [message]


@pytest.mark.parametrize(
    "number", [Decimal("999999999.999"), Decimal("-0.001"), Decimal("30.5000")]
)
def test_a_number_value_within_the_stored_precision_is_valid(number: Decimal) -> None:
    attribute = engine.AttributeIn("bag_width", number=number)
    assert engine.product_structure_errors("simple", SIZE_COLOR, (attribute,), ()) == []


@pytest.mark.parametrize(
    ("weight", "message"),
    [
        (None, None),
        (Decimal("0"), None),
        (Decimal("99999.999"), None),
        (Decimal("-1"), "cannot be negative."),
        (Decimal("0.0001"), "has more than 3 decimals."),
        (Decimal("123456"), "has more than 5 digits before the decimal point."),
        (Decimal("NaN"), "needs an exact decimal number."),
        (0.5, "needs an exact decimal number."),
    ],
)
def test_a_weight_is_not_negative_and_fits_the_stored_precision(
    weight: object, message: str | None
) -> None:
    assert engine.weight_error(weight) == message
