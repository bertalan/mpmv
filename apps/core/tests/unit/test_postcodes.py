"""SHP.12: the postcode pattern grammar shared by shipping zones, SHP.2 and the SHP.13 import.

Ranges use `..` because postcodes of some countries contain a dash (PT 1000-001, PL 00-950).
"""

import pytest

from apps.core.postcodes import (
    InvalidPostcodePattern,
    match_postcode,
    normalize_postcode,
    normalize_postcode_pattern,
    parse_postcode_pattern,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("846 00", "84600"), (" bt1 1aa ", "BT11AA"), ("9000-001", "9000001"), ("", "")],
)
def test_postcodes_are_compared_upper_case_without_spaces_and_dashes(
    raw: str, expected: str
) -> None:
    assert normalize_postcode(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("bt*", "BT*"),
        (" 846* ", "846*"),
        ("70000..74999", "70000..74999"),
        ("70000 .. 74999", "70000..74999"),
        ("1000-001..1999-999", "1000-001..1999-999"),
        ("00-000..19-999", "00-000..19-999"),
        ("84600", "84600"),
        ("846  00", "846 00"),
        ("9000-001", "9000-001"),
        ("00-950", "00-950"),
    ],
)
def test_valid_patterns_are_kept_in_a_canonical_form(raw: str, expected: str) -> None:
    assert normalize_postcode_pattern(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "*",
        "B*T",
        "BT**",
        "74999..70000",  # lower bound above the upper one
        "700..74999",  # bounds of different lengths
        "70000..",
        "..74999",
        "1..2..3",
        "70*..74999",
        "70000-74999",  # the SHP.1 range syntax: ambiguous, refused with a hint
        "9000/001",
        "X" * 33,
    ],
)
def test_malformed_patterns_are_refused(raw: str) -> None:
    with pytest.raises(InvalidPostcodePattern):
        normalize_postcode_pattern(raw)


def test_the_old_range_syntax_is_refused_with_a_hint() -> None:
    with pytest.raises(InvalidPostcodePattern, match=r"70000\.\.74999"):
        normalize_postcode_pattern("70000-74999")


@pytest.mark.parametrize(
    "raw", ["70000 - 74999", "70000 -74999", "70000- 74999", "70000  -  74999"]
)
def test_a_spaced_dash_range_is_refused_with_the_same_hint(raw: str) -> None:
    # SHP.2: spaces around the dash must not turn the old range form into an exact postcode.
    with pytest.raises(InvalidPostcodePattern, match=r"70000\.\.74999"):
        normalize_postcode_pattern(raw)
    with pytest.raises(InvalidPostcodePattern, match=r"70000\.\.74999"):
        parse_postcode_pattern(raw)


@pytest.mark.parametrize("raw", ["00-950", "9000-001", "1000 - 001", "846 00"])
def test_postcodes_with_a_dash_of_unequal_parts_stay_exact_postcodes(raw: str) -> None:
    assert parse_postcode_pattern(raw).kind == "exact"


def test_a_parsed_pattern_matches_like_match_postcode() -> None:
    parsed = parse_postcode_pattern("70000..74999")
    assert parsed.matches("70 100")
    assert parsed.matches_normalized("70100")
    assert not parsed.matches_normalized("")
    assert not parsed.matches_normalized("75000")


@pytest.mark.parametrize(
    ("pattern", "postcode", "matches"),
    [
        ("BT*", "BT1 1AA", True),
        ("BT*", "B1 1AA", False),
        ("846*", "846 00", True),
        ("70000..74999", "70100", True),
        ("70000..74999", "70000", True),
        ("70000..74999", "74999", True),
        ("70000..74999", "75000", False),
        ("70000..74999", "7010", False),  # shorter than the bounds
        ("9000..9999", "9000-001", True),  # PT islands: the first 4 characters
        ("9000..9999", "1000-001", False),
        ("1000-001..1999-999", "1500-100", True),
        ("00-000..19-999", "00-950", True),
        ("00-000..19-999", "20-001", False),
        ("84600", "846 00", True),
        ("84600", "84601", False),
        ("9000-001", "9000 001", True),
        ("BT*", "", False),
        ("70000..74999", "", False),
    ],
)
def test_match_postcode(pattern: str, postcode: str, matches: bool) -> None:
    assert match_postcode(pattern, postcode) is matches


def test_matching_refuses_a_malformed_pattern() -> None:
    with pytest.raises(InvalidPostcodePattern):
        match_postcode("74999..70000", "70100")


# --- Country-qualified patterns (SHP audit chunk 5, M1): `PT:9000..9999`, `GR:84*`


@pytest.mark.parametrize(
    ("raw", "country", "stored"),
    [
        ("PT:9000..9999", "PT", "PT:9000..9999"),
        (" gr : 84* ", "GR", "GR:84*"),
        ("EL:84600", "GR", "GR:84600"),
        ("uk:bt*", "GB", "GB:BT*"),
        ("84*", "", "84*"),
    ],
)
def test_a_pattern_may_be_qualified_by_a_country(raw: str, country: str, stored: str) -> None:
    assert parse_postcode_pattern(raw).country == country
    assert normalize_postcode_pattern(raw) == stored


@pytest.mark.parametrize("raw", ["XX:84*", "P:84*", "PRT:84*", ":84*", "PT:", "PT:PT:84*"])
def test_a_qualifier_must_be_an_iso_country_followed_by_a_pattern(raw: str) -> None:
    with pytest.raises(InvalidPostcodePattern):
        parse_postcode_pattern(raw)


def test_a_qualified_pattern_matches_the_postcode_like_the_plain_one() -> None:
    assert match_postcode("PT:9000..9999", "9000-001")
    assert not match_postcode("PT:9000..9999", "1000-001")
