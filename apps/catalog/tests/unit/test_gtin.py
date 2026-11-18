"""CAT.23: the GTIN rule (8, 12, 13 or 14 digits with the GS1 check digit), pure."""

import pytest

from apps.catalog import engine


@pytest.mark.parametrize("gtin", ["96385074", "036000291452", "4006381333931", "10614141000415"])
def test_a_gtin_with_the_right_length_and_check_digit_is_valid(gtin: str) -> None:
    assert engine.gtin_error(gtin) is None


@pytest.mark.parametrize(
    "gtin",
    [
        "4006381333932",  # wrong check digit
        "12345678",  # wrong check digit
        "963850749",  # 9 digits
        "4006381333",  # 10 digits
        "40063813339",  # 11 digits
        "400638133393100",  # 15 digits
        "40063813339A1",  # not digits
    ],
)
def test_a_gtin_with_a_wrong_check_digit_or_length_is_refused(gtin: str) -> None:
    assert engine.gtin_error(gtin) is not None


def test_an_empty_gtin_means_none() -> None:
    assert engine.gtin_error("") is None


def test_identifiers_are_stripped() -> None:
    assert engine.normalise_identifier("  4006381333931 \n") == "4006381333931"
    assert engine.normalise_identifier(None) == ""
