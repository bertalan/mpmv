"""SHP.2: legacy regression cases 1-7 (spec 06 §6.3) against `match_postcode` and `resolve_zone`.

The zones mirror the shape of the legacy configuration with synthetic data: a main carrier with
an Italy zone, numbered European zones and one "remote islands" zone defined by postcode across
GR, GB and PT; Vendor D with its own carrier and zones, `*` (World) included. The Greek island
patterns deliberately share their digits with mainland Italian postcodes (case 1).
"""

import pytest

from apps.core.countries import EU_MEMBER_STATES, InvalidCountry
from apps.core.postcodes import (
    InvalidPostcodePattern,
    PostcodePattern,
    match_postcode,
    parse_postcode_pattern,
)
from apps.shipping import engine
from apps.shipping.engine import Destination, ZoneSnap

VENDOR_A = 1  # main carrier, platform zones
VENDOR_D = 4  # own carrier, own zones

# Greek islands: Crete 70-74, North Aegean 81-83, Cyclades 84, Dodecanese 85, Ionian 28/29/31/49,
# Sporades 37. Northern Ireland BT. Madeira and the Azores 9000-9999.
REMOTE_PATTERNS = (
    "70000..74999",
    "81*",
    "82*",
    "83*",
    "84*",
    "85*",
    "28*",
    "29*",
    "31*",
    "37*",
    "49*",
    "BT*",
    "9000..9999",
)

ITALY = ZoneSnap(code="italy", priority=100, countries=frozenset({"IT"}))
ZONE_3 = ZoneSnap(code="zone-3", priority=100, countries=frozenset({"PT", "ES", "FR"}))
ZONE_4 = ZoneSnap(code="zone-4", priority=100, countries=frozenset({"GB", "IE"}))
ZONE_5 = ZoneSnap(code="zone-5", priority=100, countries=frozenset({"GR", "CY"}))
REMOTE_ISLANDS = ZoneSnap(
    code="remote-islands",
    priority=10,
    countries=frozenset({"GR", "GB", "PT"}),
    postcode_patterns=REMOTE_PATTERNS,
)
MAIN_CARRIER_ZONES = [ITALY, ZONE_3, ZONE_4, ZONE_5, REMOTE_ISLANDS]

VENDOR_D_ZONES = [
    ZoneSnap(code="d-world", priority=1, countries=frozenset({"*"}), vendor_id=VENDOR_D),
    ZoneSnap(code="d-italy", priority=100, countries=frozenset({"IT"}), vendor_id=VENDOR_D),
    ZoneSnap(code="d-europe", priority=100, group_countries=EU_MEMBER_STATES, vendor_id=VENDOR_D),
    ZoneSnap(
        code="d-non-eu", priority=100, countries=frozenset({"CH", "NO", "GB"}), vendor_id=VENDOR_D
    ),
]


def zone_for(dest: Destination, zones: list[ZoneSnap] | None = None, vendor: int = VENDOR_A) -> str:
    found = engine.resolve_zone(MAIN_CARRIER_ZONES if zones is None else zones, vendor, dest)
    assert found is not None
    return found.code


# --- Case 1: Italian postcodes that look like Greek island postcodes stay in the Italy zone


@pytest.mark.parametrize(
    "postcode", ["70123", "28100", "81100", "82100", "83100", "29121", "31100", "37010"]
)
def test_case_1_italian_postcodes_never_resolve_to_the_remote_islands_zone(postcode: str) -> None:
    # The postcode matches a remote-islands pattern, but the zone does not cover Italy.
    assert any(match_postcode(pattern, postcode) for pattern in REMOTE_PATTERNS)
    assert zone_for(Destination("IT", postcode=postcode)) == "italy"


# --- Case 2: Crete and the Cyclades are remote islands, Athens is mainland Greece


@pytest.mark.parametrize(("pattern", "postcode"), [("70000..74999", "70100"), ("84*", "84600")])
def test_case_2_greek_island_postcodes_match_their_patterns(pattern: str, postcode: str) -> None:
    assert match_postcode(pattern, postcode)


@pytest.mark.parametrize("postcode", ["70100", "84600"])
def test_case_2_greek_islands_resolve_to_the_remote_islands_zone(postcode: str) -> None:
    assert zone_for(Destination("GR", postcode=postcode)) == "remote-islands"


def test_case_2_athens_resolves_to_the_greek_mainland_zone() -> None:
    assert not any(match_postcode(pattern, "10100") for pattern in REMOTE_PATTERNS)
    assert zone_for(Destination("GR", postcode="10100")) == "zone-5"


# --- Case 3: Northern Ireland is a remote area, the rest of Great Britain is not


def test_case_3_northern_ireland_resolves_to_the_remote_islands_zone() -> None:
    assert match_postcode("BT*", "BT1 1AA")
    assert zone_for(Destination("GB", postcode="BT1 1AA")) == "remote-islands"


@pytest.mark.parametrize("postcode", ["SW1A 1AA", "EH1 1YZ", "B1 1AA"])
def test_case_3_other_british_postcodes_resolve_to_the_gb_zone(postcode: str) -> None:
    assert zone_for(Destination("GB", postcode=postcode)) == "zone-4"


# --- Case 4: Madeira and the Azores are remote islands, Lisbon is not


@pytest.mark.parametrize("postcode", ["9000-001", "9400-001", "9500-001"])
def test_case_4_portuguese_islands_resolve_to_the_remote_islands_zone(postcode: str) -> None:
    assert match_postcode("9000..9999", postcode)
    assert zone_for(Destination("PT", postcode=postcode)) == "remote-islands"


def test_case_4_lisbon_resolves_to_the_portuguese_zone() -> None:
    assert not match_postcode("9000..9999", "1000-001")
    assert zone_for(Destination("PT", postcode="1000-001")) == "zone-3"


# --- Case 5: spaces are ignored; an empty postcode falls back to the country's zone


def test_case_5_a_spaced_postcode_equals_the_unspaced_one() -> None:
    assert match_postcode("84600", "846 00")
    assert match_postcode("84*", "846 00")
    assert zone_for(Destination("GR", postcode="846 00")) == "remote-islands"


@pytest.mark.parametrize(
    ("country", "expected"), [("GR", "zone-5"), ("GB", "zone-4"), ("PT", "zone-3")]
)
def test_case_5_an_empty_postcode_resolves_to_the_countrys_zone(
    country: str, expected: str
) -> None:
    assert zone_for(Destination(country, postcode="")) == expected
    assert zone_for(Destination(country, postcode="   ")) == expected


# --- Case 6: a postcode zone applies only to the countries it lists


@pytest.mark.parametrize(
    ("country", "postcode", "expected"),
    [("IE", "BT1 1AA", "zone-4"), ("CY", "70100", "zone-5"), ("ES", "9000-001", "zone-3")],
)
def test_case_6_a_postcode_zone_applies_only_to_its_countries(
    country: str, postcode: str, expected: str
) -> None:
    assert engine.zone_match(REMOTE_ISLANDS, Destination(country, postcode=postcode)) is None
    assert zone_for(Destination(country, postcode=postcode)) == expected


# --- Case 7: "*" only as a last resort (Vendor D: USA -> World)


def test_case_7_vendor_d_ships_to_the_usa_through_its_world_zone() -> None:
    assert zone_for(Destination("US", postcode="10001"), VENDOR_D_ZONES, VENDOR_D) == "d-world"


@pytest.mark.parametrize(
    ("country", "expected"), [("IT", "d-italy"), ("DE", "d-europe"), ("CH", "d-non-eu")]
)
def test_case_7_the_world_zone_never_beats_a_matching_zone(country: str, expected: str) -> None:
    # The World zone has the best priority and still comes last.
    assert zone_for(Destination(country), VENDOR_D_ZONES, VENDOR_D) == expected


def test_case_7_vendor_d_zones_never_apply_to_another_vendor() -> None:
    assert engine.resolve_zone(VENDOR_D_ZONES, VENDOR_A, Destination("US")) is None


# --- Destination country aliases, normalised once when the destination is built


@pytest.mark.parametrize(
    ("raw", "expected"), [("EL", "GR"), ("el", "GR"), (" UK ", "GB"), ("it", "IT")]
)
def test_the_destination_country_is_normalised_to_iso_alpha2(raw: str, expected: str) -> None:
    assert Destination(raw).country == expected


def test_an_el_destination_matches_a_gr_zone() -> None:
    assert zone_for(Destination("EL", postcode="70100")) == "remote-islands"
    assert zone_for(Destination("EL", postcode="10100")) == "zone-5"


def test_a_uk_destination_matches_a_gb_zone() -> None:
    assert zone_for(Destination("UK", postcode="BT1 1AA")) == "remote-islands"
    assert zone_for(Destination("uk", postcode="SW1A 1AA")) == "zone-4"


def test_an_unknown_destination_country_is_refused() -> None:
    # Never a silent match on "*" for a country code nobody can ship to.
    with pytest.raises(InvalidCountry):
        Destination("XX")


def test_the_destination_province_is_compared_upper_case() -> None:
    sassari = ZoneSnap(code="sassari", priority=100, countries=frozenset({"IT"}), states=("SS",))
    assert Destination("IT", state=" ss ").state == "SS"
    assert engine.zone_match(sassari, Destination("IT", state="ss")) is engine.Specificity.PROVINCE


# --- Postcode patterns are parsed once per zone, not once per address


def test_a_zone_parses_its_patterns_once_when_it_is_built(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def counting_parse(raw: str) -> PostcodePattern:
        calls.append(raw)
        return parse_postcode_pattern(raw)

    monkeypatch.setattr(engine, "parse_postcode_pattern", counting_parse)
    zone = ZoneSnap(
        code="remote", priority=10, countries=frozenset({"GR"}), postcode_patterns=REMOTE_PATTERNS
    )
    assert calls == list(REMOTE_PATTERNS)
    assert [p.kind for p in zone.parsed_patterns][:2] == ["range", "prefix"]

    for postcode in ["70100", "84600", "10100", "", "846 00"]:
        engine.resolve_zone([zone], VENDOR_A, Destination("GR", postcode=postcode))
    assert calls == list(REMOTE_PATTERNS)  # no parsing while resolving


def test_a_zone_with_a_malformed_pattern_is_refused_when_it_is_built() -> None:
    with pytest.raises(InvalidPostcodePattern, match=r"70000\.\.74999"):
        ZoneSnap(
            code="bad",
            priority=10,
            countries=frozenset({"GR"}),
            postcode_patterns=("70000 - 74999",),
        )


def test_parsed_patterns_do_not_change_zone_equality() -> None:
    a = ZoneSnap(code="z", priority=1, countries=frozenset({"GR"}), postcode_patterns=("84*",))
    b = ZoneSnap(code="z", priority=1, countries=frozenset({"GR"}), postcode_patterns=("84*",))
    assert a == b
    assert hash(a) == hash(b)
    assert "parsed_patterns" not in repr(a)
