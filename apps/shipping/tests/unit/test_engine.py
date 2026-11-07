"""SHP.1: pure shipping rules behind the model constraints (zone countries, weight bands)."""

from decimal import Decimal

import pytest

from apps.core.countries import InvalidCountry
from apps.shipping import engine
from apps.shipping.models import VendorZoneRate
from apps.shipping.tests.factories import ShippingZoneFactory, WeightBandFactory


def kg(value: str) -> Decimal:
    return Decimal(value)


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
    # D-06 is open: the field offers the three policies but SHP.1 picks none as the default.
    assert set(VendorZoneRate.OverMaxWeight.values) == {"last_band", "quote", "block"}
    assert engine.OVER_MAX_WEIGHT_POLICIES == ("last_band", "quote", "block")


def test_factories_build_a_band_and_a_zone_without_the_database() -> None:
    band = WeightBandFactory.build(from_kg=kg("0"), to_kg=None)
    assert band.to_kg is None
    assert band.rate.over_max_weight is None  # D-06 hook: no policy chosen yet
    assert ShippingZoneFactory.build().countries == ["IT"]
