"""EVT.12: the geocoder chain with its cache, limiter and circuit breaker (spec 15, regression
case 24). Every provider is a `FakeGeocoder`: no test reaches the network."""

import datetime as dt
import logging
import secrets
from collections.abc import Mapping
from typing import Any

import pytest

from apps.core.geocoding import AddressQuery, GeocoderTimeout, GeocoderUnavailable
from apps.core.geocoding import services as geo
from apps.core.geocoding.adapters import LocationIQGeocoder, OpenCageGeocoder
from apps.core.geocoding.fakes import FakeGeocoder, fake_result
from apps.core.models import GeocodeCacheEntry, GeocoderState
from apps.core.tests.factories import GeocodeCacheEntryFactory, GeocoderStateFactory, utc

pytestmark = pytest.mark.django_db

T0 = utc(2027, 3, 1, 12)
MILAN = AddressQuery(country="IT", postcode="20122", city="Milano", street="Piazza del Duomo 1")


class FakeClock:
    """A clock that only moves when the code sleeps (or a test moves it)."""

    def __init__(self, start: dt.datetime) -> None:
        self.now = start
        self.slept: list[float] = []

    def __call__(self) -> dt.datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += dt.timedelta(seconds=seconds)

    def advance(self, **kwargs: float) -> None:
        self.now += dt.timedelta(**kwargs)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(T0)


def address(n: int) -> AddressQuery:
    return AddressQuery(country="IT", city="Milano", street=f"Via Roma {n}")


def lookup(query: AddressQuery, chain: list[Any], clock: FakeClock) -> geo.GeocodeOutcome:
    return geo.geocode_address(
        query, language="it", geocoders=chain, clock=clock, sleep=clock.sleep
    )


# --- Regression case 24


def test_nominatim_failing_over_to_photon_returns_photon_result(clock: FakeClock) -> None:
    nominatim = FakeGeocoder("nominatim", error=GeocoderUnavailable("down"))
    photon = FakeGeocoder("photon", results=[fake_result("photon")])
    outcome = lookup(MILAN, [nominatim, photon], clock)
    assert outcome.provider == "photon"
    assert [r.provider for r in outcome.results] == ["photon"]
    assert outcome.attempts == (("nominatim", "failed"), ("photon", "ok"))
    assert outcome.cached is False
    entry = GeocodeCacheEntry.objects.get()
    assert entry.provider == "photon"  # what EVT.1 stores as `geo_provider`
    assert entry.language == "it"


def test_a_second_lookup_of_the_same_address_is_served_from_the_cache(clock: FakeClock) -> None:
    nominatim = FakeGeocoder("nominatim", results=[fake_result("nominatim")])
    first = lookup(MILAN, [nominatim], clock)
    same = AddressQuery(
        country="it", postcode="20122", city=" MILANO ", street="piazza del  duomo 1"
    )
    second = lookup(same, [nominatim], clock)
    assert len(nominatim.calls) == 1
    assert second.cached is True
    assert second.results == first.results
    assert second.provider == "nominatim"
    assert second.attempts == ()


def test_the_cache_is_per_language(clock: FakeClock) -> None:
    nominatim = FakeGeocoder("nominatim", results=[fake_result("nominatim")])
    lookup(MILAN, [nominatim], clock)
    geo.geocode_address(MILAN, language="de", geocoders=[nominatim], clock=clock, sleep=clock.sleep)
    assert len(nominatim.calls) == 2


def test_two_nominatim_calls_in_the_same_second_are_spaced_by_the_limiter(
    clock: FakeClock,
) -> None:
    nominatim = FakeGeocoder("nominatim", results=[fake_result("nominatim")], clock=clock)
    lookup(address(1), [nominatim], clock)
    lookup(address(2), [nominatim], clock)
    assert clock.slept == [pytest.approx(1.0)]
    first, second = nominatim.call_times
    assert second - first >= dt.timedelta(seconds=1)


def test_the_limiter_is_shared_through_postgresql(clock: FakeClock) -> None:
    """Another process already took the next slot: this one waits for the one after."""
    GeocoderStateFactory.create(provider="nominatim", next_slot_at=T0 + dt.timedelta(seconds=1))
    nominatim = FakeGeocoder("nominatim", results=[fake_result("nominatim")], clock=clock)
    lookup(MILAN, [nominatim], clock)
    assert clock.slept == [pytest.approx(1.0)]
    state = GeocoderState.objects.get(provider="nominatim")
    assert state.next_slot_at == T0 + dt.timedelta(seconds=2)


def test_after_five_failures_the_breaker_skips_the_provider(clock: FakeClock) -> None:
    nominatim = FakeGeocoder("nominatim", error=GeocoderTimeout("slow"))
    photon = FakeGeocoder("photon", results=[fake_result("photon")])
    for n in range(5):
        outcome = lookup(address(n), [nominatim, photon], clock)
        assert outcome.attempts[0] == ("nominatim", "timeout")
    assert len(nominatim.calls) == 5
    outcome = lookup(address(99), [nominatim, photon], clock)
    assert len(nominatim.calls) == 5
    assert outcome.attempts == (("nominatim", "circuit_open"), ("photon", "ok"))
    assert outcome.provider == "photon"


def test_the_breaker_retries_after_five_minutes_and_a_success_closes_it(clock: FakeClock) -> None:
    GeocoderStateFactory.create(
        provider="nominatim", failures=5, open_until=T0 + dt.timedelta(minutes=5)
    )
    nominatim = FakeGeocoder("nominatim", results=[fake_result("nominatim")])
    assert lookup(address(1), [nominatim], clock).attempts == (("nominatim", "circuit_open"),)
    clock.advance(minutes=5)
    assert lookup(address(2), [nominatim], clock).attempts == (("nominatim", "ok"),)
    state = GeocoderState.objects.get(provider="nominatim")
    assert (state.failures, state.open_until) == (0, None)


def test_rate_limited_answers_count_towards_the_breaker(clock: FakeClock) -> None:
    from apps.core.geocoding import GeocoderRateLimited

    nominatim = FakeGeocoder("nominatim", error=GeocoderRateLimited("429"))
    outcome = lookup(MILAN, [nominatim], clock)
    assert outcome.attempts == (("nominatim", "rate_limited"),)
    assert GeocoderState.objects.get(provider="nominatim").failures == 1


def test_keyed_providers_are_skipped_without_a_key(clock: FakeClock) -> None:
    nominatim = FakeGeocoder("nominatim", results=[])
    photon = FakeGeocoder("photon", results=[])
    chain = [nominatim, photon, LocationIQGeocoder(key=""), OpenCageGeocoder(key="")]
    outcome = lookup(MILAN, chain, clock)
    assert outcome.attempts == (
        ("nominatim", "empty"),
        ("photon", "empty"),
        ("locationiq", "no_key"),
        ("opencage", "no_key"),
    )
    assert outcome.results == ()
    assert outcome.provider is None
    assert not GeocodeCacheEntry.objects.exists()  # nothing found, nothing cached


# --- Budget, timeouts and settings


def test_each_call_gets_at_most_the_per_call_timeout(clock: FakeClock) -> None:
    nominatim = FakeGeocoder("nominatim", results=[fake_result("nominatim")])
    lookup(MILAN, [nominatim], clock)
    assert nominatim.timeouts == [3.0]


def test_the_chain_stops_when_its_budget_is_spent(clock: FakeClock) -> None:
    class SlowGeocoder(FakeGeocoder):
        def geocode(self, query: AddressQuery, **kwargs: Any) -> Any:
            clock.advance(seconds=9.5)
            return super().geocode(query, **kwargs)

    slow = SlowGeocoder("nominatim", error=GeocoderTimeout("slow"))
    photon = FakeGeocoder("photon", results=[fake_result("photon")])
    opencage = FakeGeocoder("opencage", results=[fake_result("opencage")])
    outcome = lookup(MILAN, [slow, photon, opencage], clock)
    assert photon.timeouts == [pytest.approx(0.5)]
    assert outcome.provider == "photon"

    clock.advance(minutes=1)
    slow_again = SlowGeocoder("photon", error=GeocoderTimeout("slow"))
    slower = SlowGeocoder("opencage", error=GeocoderTimeout("slow"))
    last = FakeGeocoder("locationiq", results=[fake_result("locationiq")])
    outcome = lookup(address(5), [slow_again, slower, last], clock)
    assert outcome.attempts[-1] == ("locationiq", "budget_exhausted")
    assert last.calls == []


def test_a_wait_longer_than_the_budget_skips_the_provider(clock: FakeClock) -> None:
    GeocoderStateFactory.create(provider="nominatim", next_slot_at=T0 + dt.timedelta(seconds=30))
    nominatim = FakeGeocoder("nominatim", results=[fake_result("nominatim")])
    photon = FakeGeocoder("photon", results=[fake_result("photon")])
    outcome = lookup(MILAN, [nominatim, photon], clock)
    assert outcome.attempts == (("nominatim", "throttled"), ("photon", "ok"))
    assert clock.slept == []
    assert GeocoderState.objects.get(provider="nominatim").failures == 0


def test_an_expired_cache_entry_is_not_used(clock: FakeClock) -> None:
    from apps.core.geocoding import engine

    GeocodeCacheEntryFactory.create(
        key=engine.cache_key(MILAN), language="it", expires_at=T0 - dt.timedelta(seconds=1)
    )
    nominatim = FakeGeocoder("nominatim", results=[fake_result("nominatim")])
    outcome = lookup(MILAN, [nominatim], clock)
    assert outcome.cached is False
    assert len(nominatim.calls) == 1
    entry = GeocodeCacheEntry.objects.get()
    assert entry.expires_at == T0 + dt.timedelta(days=180)
    assert geo.purge_expired_geocode_cache(now=T0 + dt.timedelta(days=181)) == 1


def test_the_chain_comes_from_the_settings(settings: Any) -> None:
    settings.GEOCODING_PROVIDERS = ["nominatim", "photon", "locationiq", "opencage"]
    settings.GEOCODING_LOCATIONIQ_KEY = ""
    settings.GEOCODING_OPENCAGE_KEY = ""
    chain = geo.configured_geocoders()
    assert [g.name for g in chain] == ["nominatim", "photon", "locationiq", "opencage"]
    assert [g.configured for g in chain] == [True, True, False, False]
    settings.GEOCODING_PROVIDERS = ["photon"]
    assert [g.name for g in geo.configured_geocoders()] == ["photon"]


def test_the_package_level_geocode_uses_the_configured_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.core.geocoding import geocode

    photon = FakeGeocoder("photon", results=[fake_result("photon")])
    monkeypatch.setattr(geo, "configured_geocoders", lambda: [photon])
    assert geocode(MILAN, language="en").provider == "photon"


# --- Secrets


def test_a_key_never_appears_in_the_logs(
    clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    key = secrets.token_urlsafe(24)

    def leaky_transport(
        url: str, params: Mapping[str, str], headers: Mapping[str, str], timeout: float
    ) -> Any:
        raise ValueError(f"cannot fetch {url}?key={params['key']}")

    chain = [
        LocationIQGeocoder(key=key, transport=leaky_transport),
        OpenCageGeocoder(key=key, transport=leaky_transport),
    ]
    with caplog.at_level(logging.DEBUG):
        outcome = lookup(MILAN, chain, clock)
    assert outcome.attempts == (("locationiq", "failed"), ("opencage", "failed"))
    assert caplog.records, "failures are logged"
    assert key not in caplog.text
    assert key not in repr(outcome)
