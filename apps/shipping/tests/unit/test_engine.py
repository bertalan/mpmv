"""SHP.1 and SHP.12: pure shipping rules behind the models (zone countries, weight bands, zone
precedence for vendor zones, country groups and postcodes)."""

from decimal import Decimal

import pytest

from apps.core.countries import InvalidCountry
from apps.shipping import engine
from apps.shipping.engine import Destination, Specificity, ZoneSnap
from apps.shipping.models import ShippingMethod
from apps.shipping.tests.factories import (
    ShippingMethodFactory,
    ShippingZoneFactory,
    WeightBandFactory,
)

VENDOR = 1
OTHER_VENDOR = 2


def kg(value: str) -> Decimal:
    return Decimal(value)


def zone(code: str, *countries: str, priority: int = 100, **fields: object) -> ZoneSnap:
    return ZoneSnap(code=code, priority=priority, countries=frozenset(countries), **fields)  # type: ignore[arg-type]


def resolved(zones: list[ZoneSnap], dest: Destination, vendor_id: int = VENDOR) -> str | None:
    found = engine.resolve_zone(zones, vendor_id, dest)
    return None if found is None else found.code


# --- Countries and bands (SHP.1)


@pytest.mark.parametrize(
    ("raw", "expected"), [("it", "IT"), (" gr ", "GR"), ("EL", "GR"), ("uk", "GB")]
)
def test_zone_countries_are_normalized_to_iso_alpha2(raw: str, expected: str) -> None:
    assert engine.normalize_zone_country(raw) == expected


def test_the_rest_of_the_world_marker_is_kept() -> None:
    assert engine.normalize_zone_country(" * ") == engine.WORLD == "*"


def test_an_unknown_zone_country_is_refused() -> None:
    with pytest.raises(InvalidCountry):
        engine.normalize_zone_country("XX")


def test_adjacent_bands_do_not_overlap_because_bounds_are_right_inclusive() -> None:
    # Spec 06 rule 3: 5.000 kg is in 0-5, 5.001 kg is in 5-10.
    assert not engine.bands_overlap((kg("0"), kg("5")), (kg("5"), kg("10")))


def test_bands_sharing_any_weight_overlap() -> None:
    assert engine.bands_overlap((kg("0"), kg("5")), (kg("4.999"), kg("10")))
    assert engine.bands_overlap((kg("0"), kg("10")), (kg("2"), kg("3")))


def test_an_unlimited_band_covers_every_weight_above_its_start() -> None:
    unlimited = (kg("10"), None)
    assert engine.bands_overlap(unlimited, (kg("20"), kg("30")))
    assert engine.bands_overlap(unlimited, (kg("20"), None))
    assert not engine.bands_overlap(unlimited, (kg("5"), kg("10")))


def test_overlapping_bands_reports_each_overlapping_pair() -> None:
    bands = [(kg("0"), kg("5")), (kg("5"), kg("10")), (kg("8"), None), (kg("20"), kg("30"))]
    assert engine.overlapping_bands(bands) == [(1, 2), (2, 3)]
    assert engine.overlapping_bands(bands[:2]) == []


def test_over_max_weight_policies_are_the_ones_listed_for_d06() -> None:
    # D-06 is open: the field offers the three policies but picks none as the default.
    assert set(ShippingMethod.OverMaxWeight.values) == {"last_band", "quote", "block"}
    assert engine.OVER_MAX_WEIGHT_POLICIES == ("last_band", "quote", "block")


def test_factories_build_a_band_a_method_and_a_zone_without_the_database() -> None:
    band = WeightBandFactory.build(from_kg=kg("0"), to_kg=None)
    assert band.to_kg is None
    assert band.method.over_max_weight == engine.PLATFORM_DEFAULT  # D-06 hook: no policy yet
    assert ShippingZoneFactory.build().countries == ["IT"]
    assert ShippingZoneFactory.build().vendor is None  # platform zone by default
    express = ShippingMethodFactory.build(express=True)
    assert (express.service_level, express.max_delivery_days) == (engine.EXPRESS, 2)


# --- Methods (SHP.12)


def test_service_levels_and_tax_treatments_are_the_listed_choices() -> None:
    assert set(ShippingMethod.ServiceLevel.values) == {"standard", "express", "economy", "other"}
    assert set(ShippingMethod.TaxTreatment.values) == {"follows_goods", "standard_rate", "exempt"}
    # Default: shipping follows the VAT of the goods (EU ancillary-supply rule). D-03 is open:
    # the treatment is stored, the tax engine (TAX.4) applies it.
    assert engine.DEFAULT_TAX_TREATMENT == engine.FOLLOWS_GOODS == "follows_goods"


@pytest.mark.parametrize(("raw", "expected"), [(" ss ", "SS"), ("it-ss", "IT-SS"), ("Ca", "CA")])
def test_provinces_are_normalized_upper_case(raw: str, expected: str) -> None:
    assert engine.normalize_state(raw) == expected


@pytest.mark.parametrize("raw", ["", "  ", "S S", "SS*", "X" * 33])
def test_malformed_provinces_are_refused(raw: str) -> None:
    with pytest.raises(engine.InvalidState):
        engine.normalize_state(raw)


# --- Zone matching: specificity of one zone for one address


def test_a_zone_matches_with_the_most_specific_reason() -> None:
    italy = Destination(country="IT", postcode="07100", state="SS")
    assert engine.zone_match(zone("it", "IT"), italy) is Specificity.COUNTRY
    eu = zone("eu", group_countries=frozenset({"IT", "DE"}))
    assert engine.zone_match(eu, italy) is Specificity.GROUP
    sardinia = zone("sardinia", "IT", postcode_patterns=("07000..09999",))
    assert engine.zone_match(sardinia, italy) is Specificity.POSTCODE
    sassari = zone("sassari", "IT", states=("SS",))
    assert engine.zone_match(sassari, italy) is Specificity.PROVINCE
    assert engine.zone_match(zone("world", "*"), italy) is Specificity.WORLD


def test_a_narrowed_zone_does_not_match_outside_its_postcodes_or_provinces() -> None:
    sardinia = zone("sardinia", "IT", postcode_patterns=("07000..09999",), states=("SS",))
    assert engine.zone_match(sardinia, Destination("IT", postcode="20100", state="MI")) is None
    assert engine.zone_match(sardinia, Destination("IT", postcode="", state="SS")) is (
        Specificity.PROVINCE
    )
    # An address without a postcode never matches a postcode-only zone (case 5).
    northern_ireland = zone("bt", "GB", postcode_patterns=("BT*",))
    assert engine.zone_match(northern_ireland, Destination("GB")) is None


def test_a_postcode_zone_applies_only_to_its_countries() -> None:
    # Spec 06 case 6: "BT*" in a GB zone says nothing about other countries.
    gb_islands = zone("gb-islands", "GB", postcode_patterns=("BT*",))
    assert engine.zone_match(gb_islands, Destination("IE", postcode="BT1 1AA")) is None


def test_a_group_zone_covers_every_member_country() -> None:
    eu = zone("eu", group_countries=frozenset({"IT", "DE", "PT"}))
    assert engine.zone_match(eu, Destination("PT", postcode="1000-001")) is Specificity.GROUP
    assert engine.zone_match(eu, Destination("CH")) is None


# --- Zone precedence (spec 06 §6.2): vendor zone, priority, specificity, "*" last


def test_a_matching_vendor_zone_wins_over_platform_zones() -> None:
    platform_islands = zone("it-islands", "IT", priority=1, postcode_patterns=("07000..09999",))
    vendor_zone = zone("my-italy", "IT", priority=500, vendor_id=VENDOR)
    zones = [platform_islands, vendor_zone]
    # Worse priority and a less specific match: the vendor zone still wins for its vendor.
    assert resolved(zones, Destination("IT", postcode="07100")) == "my-italy"


def test_another_vendor_keeps_the_platform_zone_at_the_same_address() -> None:
    platform_italy = zone("it", "IT")
    vendor_zone = zone("my-sardinia", "IT", postcode_patterns=("07*",), vendor_id=VENDOR)
    dest = Destination("IT", postcode="07100")
    assert resolved([platform_italy, vendor_zone], dest, VENDOR) == "my-sardinia"
    assert resolved([platform_italy, vendor_zone], dest, OTHER_VENDOR) == "it"


def test_a_vendor_zone_that_does_not_match_leaves_the_platform_zone() -> None:
    platform_italy = zone("it", "IT")
    vendor_zone = zone("my-sardinia", "IT", postcode_patterns=("07*",), vendor_id=VENDOR)
    assert resolved([platform_italy, vendor_zone], Destination("IT", postcode="20100")) == "it"


def test_priority_comes_before_specificity() -> None:
    country = zone("it", "IT", priority=10)
    postcode = zone("it-south", "IT", priority=20, postcode_patterns=("70000..74999",))
    assert resolved([country, postcode], Destination("IT", postcode="70100")) == "it"


def test_at_the_same_priority_the_most_specific_match_wins() -> None:
    dest = Destination("IT", postcode="07100", state="SS")
    zones = [
        zone("eu", group_countries=frozenset({"IT"})),
        zone("it", "IT"),
        zone("sassari", "IT", states=("SS",)),
        zone("sardinia", "IT", postcode_patterns=("07000..09999",)),
    ]
    assert resolved(zones, dest) == "sardinia"
    assert resolved(zones[:3], dest) == "sassari"
    assert resolved(zones[:2], dest) == "it"
    assert resolved(zones[:1], dest) == "eu"


def test_the_rest_of_the_world_is_only_a_last_resort() -> None:
    # Spec 06 case 7: "*" only when nothing else matches, whatever its priority.
    world = zone("world", "*", priority=1)
    italy = zone("it", "IT", priority=999)
    assert resolved([world, italy], Destination("IT")) == "it"
    assert resolved([world, italy], Destination("US")) == "world"


def test_a_vendor_world_zone_never_hides_a_matching_platform_zone() -> None:
    vendor_world = zone("my-world", "*", vendor_id=VENDOR)
    platform_world = zone("world", "*")
    italy = zone("it", "IT")
    zones = [vendor_world, platform_world, italy]
    assert resolved(zones, Destination("IT")) == "it"
    assert resolved(zones, Destination("US")) == "my-world"  # the vendor's "*" first
    assert resolved(zones, Destination("US"), OTHER_VENDOR) == "world"


def test_another_vendors_zone_is_never_a_candidate() -> None:
    theirs = zone("their-italy", "IT", vendor_id=OTHER_VENDOR)
    assert resolved([theirs], Destination("IT")) is None


def test_no_matching_zone_resolves_to_none() -> None:
    assert resolved([zone("it", "IT")], Destination("FR")) is None
    assert resolved([], Destination("IT")) is None
