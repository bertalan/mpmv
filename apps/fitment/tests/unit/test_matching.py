"""FIT.2: the pure matching engine, spec 17 §17.2.3 and regression cases 1 and 2 (§17.11)."""

from apps.fitment.engine import (
    CHECK,
    FITS,
    NO,
    UNKNOWN,
    Profile,
    Row,
    answer,
    check,
    fits,
    no,
    unknown,
)

MAKE = "0001"
MODEL_A = "00010001"
MODEL_B = "00010002"
VARIANT_1 = "000100010001"
VARIANT_2 = "000100010002"


def test_case_1_a_year_range_gives_fits_no_and_check() -> None:
    rows = [Row(MODEL_A, year_from=2016, year_to=2021)]
    assert answer("specific", rows, Profile(MODEL_A, 2019)) == FITS
    assert answer("specific", rows, Profile(MODEL_A, 2016)) == FITS
    assert answer("specific", rows, Profile(MODEL_A, 2021)) == FITS
    assert answer("specific", rows, Profile(MODEL_A, 2022)) == NO
    assert answer("specific", rows, Profile(MODEL_A, 2015)) == NO
    assert answer("specific", rows, Profile(MODEL_A, None)) == CHECK  # the row has a range
    assert answer("specific", rows, Profile(MAKE, 2019)) == CHECK  # the profile is less specific
    assert answer("specific", rows, Profile(MAKE, None)) == CHECK
    assert answer("specific", rows, Profile(MODEL_B, 2019)) == NO  # a sibling is not covered


def test_a_row_without_years_covers_every_year_and_a_profile_without_year() -> None:
    rows = [Row(MODEL_A)]
    assert answer("specific", rows, Profile(MODEL_A, 1990)) == FITS
    assert answer("specific", rows, Profile(MODEL_A, None)) == FITS


def test_an_include_row_covers_the_nodes_below_it() -> None:
    rows = [Row(MAKE)]
    assert answer("specific", rows, Profile(VARIANT_1, 2020)) == FITS
    assert answer("specific", rows, Profile(MODEL_A, None)) == FITS


def test_case_2_an_exclusion_under_an_include_gives_fits_and_no() -> None:
    rows = [Row(MODEL_A), Row(VARIANT_2, is_exclusion=True)]
    assert answer("specific", rows, Profile(VARIANT_1, 2020)) == FITS
    assert answer("specific", rows, Profile(VARIANT_2, 2020)) == NO
    # The profile only knows the model: one variant is excluded, so the fit is not certain.
    assert answer("specific", rows, Profile(MODEL_A, 2020)) == CHECK


def test_an_exclusion_at_an_ancestor_beats_an_include_below_it() -> None:
    rows = [Row(VARIANT_1), Row(MODEL_A, is_exclusion=True)]
    assert answer("specific", rows, Profile(VARIANT_1, 2020)) == NO


def test_an_exclusion_with_years_only_excludes_those_years() -> None:
    rows = [Row(MODEL_A), Row(MODEL_A, is_exclusion=True, year_from=2018, year_to=2019)]
    assert answer("specific", rows, Profile(MODEL_A, 2017)) == FITS
    assert answer("specific", rows, Profile(MODEL_A, 2018)) == NO
    assert answer("specific", rows, Profile(MODEL_A, None)) == CHECK


def test_a_make_only_profile_is_never_fits() -> None:
    rows = [Row(MODEL_A), Row(MODEL_B)]
    assert answer("specific", rows, Profile(MAKE, 2020)) == CHECK


def test_universal_fits_everything_and_undeclared_is_unknown() -> None:
    assert answer("universal", [], Profile(VARIANT_2, 1950)) == FITS
    assert answer("universal", [], Profile(MAKE, None)) == FITS
    assert answer("undeclared", [], Profile(MODEL_A, 2020)) == UNKNOWN


def test_specific_with_nothing_above_is_no() -> None:
    assert answer("specific", [], Profile(MODEL_A, 2020)) == NO


def test_the_four_predicates_agree_with_the_answer() -> None:
    rows = [Row(MODEL_A, year_from=2016, year_to=2021)]
    assert fits("specific", rows, Profile(MODEL_A, 2019))
    assert no("specific", rows, Profile(MODEL_A, 2022))
    assert check("specific", rows, Profile(MODEL_A, None))
    assert unknown("undeclared", [], Profile(MODEL_A, 2019))
    assert not fits("specific", rows, Profile(MODEL_A, 2022))
