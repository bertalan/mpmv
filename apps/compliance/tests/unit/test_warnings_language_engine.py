"""CMP.3, owner decision 2026-11-09 (Q2): warnings in a language of the destination country.

GPSR article 19(c): when a product has warnings, they must be in a language that consumers of
the destination country understand. A product with warnings is not sold into a country until
its warnings exist in one of that country's languages; it stays published and on sale for the
other countries. A product without warnings is not concerned. Countries without an entry in the
language table (outside the EU and EEA, where GPSR does not apply) are not checked.
"""

import pytest

from apps.compliance import engine
from apps.core.countries import EU_MEMBER_STATES, OFFICIAL_LANGUAGES

# Synthetic languages: the engine takes the table as data.
TABLE = {"AT": ("xx",), "BE": ("yy", "zz"), "CH": ("xx", "yy")}


def test_without_warnings_every_destination_is_allowed() -> None:
    check = engine.warnings_sale_check(frozenset(), "AT", TABLE)
    assert check.allowed
    assert check.reason == engine.NO_WARNINGS


def test_warnings_in_a_language_of_the_destination_allow_the_sale() -> None:
    check = engine.warnings_sale_check(frozenset({"zz"}), "BE", TABLE)
    assert check.allowed
    assert check.reason == engine.WARNINGS_AVAILABLE
    assert check.country_languages == ("yy", "zz")


def test_warnings_only_in_other_languages_block_that_destination() -> None:
    check = engine.warnings_sale_check(frozenset({"xx"}), "BE", TABLE)
    assert not check.allowed
    assert check.reason == engine.WARNINGS_LANGUAGE_MISSING
    assert check.country == "BE"
    assert check.country_languages == ("yy", "zz")
    assert check.available_languages == ("xx",)


def test_regional_variants_match_their_base_language() -> None:
    assert engine.warnings_sale_check(frozenset({"yy-qq"}), "BE", TABLE).allowed


def test_a_destination_outside_the_table_is_not_checked() -> None:
    check = engine.warnings_sale_check(frozenset({"xx"}), "US", TABLE)
    assert check.allowed
    assert check.reason == engine.OUTSIDE_SCOPE


def test_the_destination_is_normalised() -> None:
    table = {"GR": ("el",)}
    assert engine.warnings_sale_check(frozenset({"el"}), " el ", table).allowed  # EL is GR


def test_an_invalid_destination_is_refused() -> None:
    with pytest.raises(ValueError, match="country"):
        engine.warnings_sale_check(frozenset({"xx"}), "ZZZ", TABLE)


def test_language_gaps_list_the_blocked_countries() -> None:
    gaps = engine.warnings_language_gaps(frozenset({"xx"}), TABLE)
    assert gaps == {"BE": ("yy", "zz")}
    assert engine.warnings_language_gaps(frozenset(), TABLE) == {}
    assert engine.warnings_language_gaps(frozenset({"xx", "yy"}), TABLE) == {}


def test_the_language_table_covers_every_member_state_in_lower_case() -> None:
    assert set(OFFICIAL_LANGUAGES) >= EU_MEMBER_STATES
    for country, languages in OFFICIAL_LANGUAGES.items():
        assert languages, country
        assert all(code == code.lower() and len(code) == 2 for code in languages), country
