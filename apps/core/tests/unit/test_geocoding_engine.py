"""EVT.12: pure geocoding rules (normalised address, circuit breaker, limiter slots, parsers)."""

import datetime as dt
from decimal import Decimal

import pytest

from apps.core.geocoding import AddressQuery, GeocodeResult, engine

T0 = dt.datetime(2027, 3, 1, 12, tzinfo=dt.UTC)


def test_the_normalised_address_folds_case_and_collapses_whitespace() -> None:
    a = AddressQuery(country="it", postcode=" 20121 ", city="MILANO", street="Via  Roma   1")
    b = AddressQuery(country="IT", postcode="20121", city="milano", street="via roma 1")
    assert engine.normalised_address(a) == engine.normalised_address(b)
    assert engine.cache_key(a) == engine.cache_key(b)
    assert len(engine.cache_key(a)) == 64


def test_different_addresses_have_different_keys() -> None:
    a = AddressQuery(country="IT", city="Milano", street="Via Roma 1")
    b = AddressQuery(country="IT", city="Milano", street="Via Roma 2")
    assert engine.cache_key(a) != engine.cache_key(b)


def test_an_address_query_without_any_field_is_empty() -> None:
    assert AddressQuery().is_empty()
    assert AddressQuery(country="IT").is_empty()  # a country alone is not an address
    assert not AddressQuery(city="Milano").is_empty()


def test_the_query_text_joins_the_typed_fields() -> None:
    query = AddressQuery(country="IT", postcode="20121", city="Milano", street="Via Roma 1")
    assert query.text() == "Via Roma 1, 20121 Milano"


def test_the_breaker_opens_after_the_threshold_and_closes_after_the_cooldown() -> None:
    state = engine.BreakerState()
    cooldown = dt.timedelta(minutes=5)
    for _ in range(4):
        state = engine.breaker_after_failure(state, T0, threshold=5, cooldown=cooldown)
        assert not engine.breaker_is_open(state, T0)
    state = engine.breaker_after_failure(state, T0, threshold=5, cooldown=cooldown)
    assert engine.breaker_is_open(state, T0)
    assert engine.breaker_is_open(state, T0 + dt.timedelta(minutes=4, seconds=59))
    assert not engine.breaker_is_open(state, T0 + cooldown)  # half open: one try allowed


def test_a_failed_try_after_the_cooldown_opens_the_breaker_again() -> None:
    cooldown = dt.timedelta(minutes=5)
    state = engine.BreakerState(failures=5, open_until=T0)
    later = T0 + dt.timedelta(minutes=1)
    state = engine.breaker_after_failure(state, later, threshold=5, cooldown=cooldown)
    assert state.open_until == later + cooldown


def test_the_limiter_hands_out_spaced_slots() -> None:
    slot, next_free = engine.reserve_slot(None, T0, interval=1.0)
    assert (slot, next_free) == (T0, T0 + dt.timedelta(seconds=1))
    slot, next_free = engine.reserve_slot(next_free, T0, interval=1.0)
    assert slot == T0 + dt.timedelta(seconds=1)
    assert next_free == T0 + dt.timedelta(seconds=2)
    later = T0 + dt.timedelta(seconds=10)
    assert engine.reserve_slot(next_free, later, interval=1.0)[0] == later


def test_coordinates_keep_six_decimal_places() -> None:
    assert engine.coordinate("45.46427123") == Decimal("45.464271")
    assert engine.coordinate(9.1895) == Decimal("9.189500")
    with pytest.raises(ValueError, match="out of range"):
        engine.coordinate("91")
    with pytest.raises(ValueError, match="not a coordinate"):
        engine.coordinate("abc")


def test_nominatim_answers_become_results() -> None:
    payload = [
        {
            "lat": "45.4642700",
            "lon": "9.1895100",
            "display_name": "Piazza del Duomo, Milano",
            "addresstype": "road",
            "category": "highway",
            "type": "pedestrian",
        },
        {"lat": "45.4", "lon": "9.1", "display_name": "Milano", "addresstype": "city"},
        {"lat": "bad", "lon": "9.1", "display_name": "broken"},  # skipped
    ]
    results = engine.parse_nominatim(payload, provider="nominatim")
    assert results == [
        GeocodeResult(
            latitude=Decimal("45.464270"),
            longitude=Decimal("9.189510"),
            label="Piazza del Duomo, Milano",
            precision="street",
            confidence=None,
            provider="nominatim",
        ),
        GeocodeResult(
            latitude=Decimal("45.400000"),
            longitude=Decimal("9.100000"),
            label="Milano",
            precision="locality",
            confidence=None,
            provider="nominatim",
        ),
    ]


def test_photon_answers_become_results_filtered_by_country() -> None:
    payload = {
        "features": [
            {
                "geometry": {"coordinates": [9.18951, 45.46427]},
                "properties": {
                    "type": "house",
                    "name": "Duomo",
                    "street": "Piazza del Duomo",
                    "housenumber": "1",
                    "postcode": "20122",
                    "city": "Milano",
                    "countrycode": "IT",
                },
            },
            {
                "geometry": {"coordinates": [9.0, 46.0]},
                "properties": {"type": "city", "name": "Elsewhere", "countrycode": "CH"},
            },
        ]
    }
    results = engine.parse_photon(payload, provider="photon", country="it")
    assert len(results) == 1
    assert results[0].latitude == Decimal("45.464270")
    assert results[0].longitude == Decimal("9.189510")
    assert results[0].precision == "rooftop"
    assert results[0].label == "Duomo, Piazza del Duomo 1, 20122 Milano"


def test_opencage_answers_become_results_with_a_confidence() -> None:
    payload = {
        "results": [
            {
                "geometry": {"lat": 45.46427, "lng": 9.18951},
                "formatted": "Piazza del Duomo, 20122 Milano, Italy",
                "confidence": 9,
                "components": {"_type": "road"},
            }
        ]
    }
    [result] = engine.parse_opencage(payload, provider="opencage")
    assert result.confidence == pytest.approx(0.9)
    assert result.precision == "street"


@pytest.mark.parametrize(
    ("kind", "precision"),
    [
        ("house", "rooftop"),
        ("building", "rooftop"),
        ("road", "street"),
        ("street", "street"),
        ("postcode", "postcode"),
        ("village", "locality"),
        ("suburb", "locality"),
        ("state", "region"),
        ("something", "locality"),
    ],
)
def test_osm_kinds_map_to_a_precision(kind: str, precision: str) -> None:
    assert engine.precision_of(kind) == precision


def test_the_user_agent_identifies_the_platform_and_its_contact() -> None:
    agent = engine.user_agent("https://shop.example.com", "ops@example.com")
    assert agent.startswith("MPMV/")
    assert "https://shop.example.com" in agent
    assert "ops@example.com" in agent


def test_results_round_trip_through_the_cache_format() -> None:
    result = GeocodeResult(
        latitude=Decimal("45.464270"),
        longitude=Decimal("9.189510"),
        label="Duomo",
        precision="rooftop",
        confidence=0.5,
        provider="photon",
    )
    assert GeocodeResult.from_dict(result.to_dict()) == result
    assert result.to_dict()["latitude"] == "45.464270"
