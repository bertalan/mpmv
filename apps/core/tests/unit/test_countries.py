"""Country codes are ISO 3166-1 alpha-2 (spec 01): GR not EL, GB not UK."""

import pytest

from apps.core.countries import EU_MEMBER_STATES, InvalidCountry, is_eu, normalize_country


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("it", "IT"), (" de ", "DE"), ("EL", "GR"), ("el", "GR"), ("UK", "GB"), ("GB", "GB")],
)
def test_normalize_country(raw: str, expected: str) -> None:
    assert normalize_country(raw) == expected


@pytest.mark.parametrize("raw", ["", "ITA", "ZZ", "X1", "XI"])
def test_normalize_country_rejects_non_iso_codes(raw: str) -> None:
    with pytest.raises(InvalidCountry):
        normalize_country(raw)


def test_eu_has_27_member_states_with_iso_codes() -> None:
    assert len(EU_MEMBER_STATES) == 27
    assert "GR" in EU_MEMBER_STATES
    assert "EL" not in EU_MEMBER_STATES


@pytest.mark.parametrize(
    ("code", "expected"), [("IT", True), ("GR", True), ("GB", False), ("CH", False)]
)
def test_is_eu(code: str, expected: bool) -> None:
    assert is_eu(code) is expected
