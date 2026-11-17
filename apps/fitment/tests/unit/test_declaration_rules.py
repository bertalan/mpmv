"""FIT.2: the validation rules of spec 17 §17.2.4, pure."""

from apps.fitment.engine import Row, declaration_problems

MAKE = "0001"
MODEL_A = "00010001"
VARIANT_2 = "000100010002"


def errors(mode: str, rows: list[Row], **kwargs: object) -> list[str]:
    return declaration_problems(mode, rows, max_rows=500, **kwargs).errors  # type: ignore[arg-type]


def warnings(mode: str, rows: list[Row]) -> list[str]:
    return declaration_problems(mode, rows, max_rows=500).warnings


def test_a_clean_specific_declaration_has_no_problem() -> None:
    problems = declaration_problems(
        "specific", [Row(MODEL_A, year_from=2016, year_to=2021)], max_rows=5
    )
    assert problems.errors == []
    assert problems.warnings == []


def test_unknown_mode_is_refused() -> None:
    assert errors("sometimes", [])


def test_specific_needs_an_include_row() -> None:
    assert errors("specific", [])
    assert errors("specific", [Row(MODEL_A, is_exclusion=True)])


def test_universal_rejects_include_rows_but_needs_none() -> None:
    assert errors("universal", []) == []
    assert errors("universal", [Row(MODEL_A)])


def test_undeclared_takes_no_rows() -> None:
    assert errors("undeclared", []) == []
    assert errors("undeclared", [Row(MODEL_A)])


def test_year_from_after_year_to_is_an_error() -> None:
    assert errors("specific", [Row(MODEL_A, year_from=2021, year_to=2016)])


def test_years_must_sit_inside_the_scheme_range() -> None:
    rows = [Row(MODEL_A, year_from=1850, year_to=2000)]
    assert errors("specific", rows, year_min=1900, year_max=2030)
    rows = [Row(MODEL_A, year_from=2000, year_to=2040)]
    assert errors("specific", rows, year_min=1900, year_max=2030)
    assert (
        errors(
            "specific", [Row(MODEL_A, year_from=1900, year_to=2030)], year_min=1900, year_max=2030
        )
        == []
    )


def test_years_are_refused_when_the_scheme_has_no_year() -> None:
    assert errors("specific", [Row(MODEL_A, year_from=2000, year_to=2001)], years_enabled=False)


def test_an_overlapping_contradictory_row_is_an_error() -> None:
    rows = [
        Row(MODEL_A, year_from=2016, year_to=2021),
        Row(MODEL_A, is_exclusion=True, year_from=2020, year_to=2023),
    ]
    assert errors("specific", rows)
    assert errors("specific", [Row(MODEL_A), Row(MODEL_A, is_exclusion=True)])


def test_the_same_node_with_disjoint_years_and_opposite_flags_is_fine() -> None:
    rows = [
        Row(MODEL_A, year_from=2016, year_to=2019),
        Row(MODEL_A, is_exclusion=True, year_from=2020, year_to=2023),
    ]
    assert errors("specific", rows) == []


def test_a_duplicate_row_is_an_error() -> None:
    assert errors("specific", [Row(MODEL_A), Row(MODEL_A)])


def test_too_many_rows_is_an_error() -> None:
    rows = [Row(f"0001{n:04d}") for n in range(4)]
    assert declaration_problems("specific", rows, max_rows=3).errors


def test_an_exclusion_without_anything_to_exclude_from_is_a_warning() -> None:
    rows = [Row(MODEL_A), Row("00010002" + "0001", is_exclusion=True)]
    assert any("exclusion" in w for w in warnings("specific", rows))
    covered = [Row(MODEL_A), Row(VARIANT_2, is_exclusion=True)]
    assert warnings("specific", covered) == []
    assert warnings("universal", [Row(MODEL_A, is_exclusion=True)]) == []
