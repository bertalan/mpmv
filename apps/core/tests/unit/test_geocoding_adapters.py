"""EVT.12: provider adapters behind the `Geocoder` protocol. No test reaches the network: the
adapters get a recording transport, and the HTTP transport is tested with `urlopen` mocked."""

import io
import secrets
import urllib.error
from collections.abc import Mapping
from typing import Any

import pytest
from pytest_django.fixtures import SettingsWrapper

from apps.core.geocoding import (
    AddressQuery,
    Geocoder,
    GeocoderRateLimited,
    GeocoderTimeout,
    GeocoderUnavailable,
    adapters,
)
from apps.core.geocoding.fakes import FakeGeocoder, fake_result

QUERY = AddressQuery(country="IT", postcode="20122", city="Milano", street="Piazza del Duomo 1")


class RecordingTransport:
    def __init__(self, answer: Any) -> None:
        self.answer = answer
        self.calls: list[tuple[str, dict[str, str], dict[str, str], float]] = []

    def __call__(
        self, url: str, params: Mapping[str, str], headers: Mapping[str, str], timeout: float
    ) -> Any:
        self.calls.append((url, dict(params), dict(headers), timeout))
        return self.answer


def test_every_adapter_and_the_fake_follow_the_protocol() -> None:
    key = secrets.token_urlsafe(16)
    for geocoder in (
        adapters.NominatimGeocoder(user_agent="MPMV/test", transport=RecordingTransport([])),
        adapters.PhotonGeocoder(user_agent="MPMV/test", transport=RecordingTransport({})),
        adapters.LocationIQGeocoder(key=key, transport=RecordingTransport([])),
        adapters.OpenCageGeocoder(key=key, transport=RecordingTransport({})),
        FakeGeocoder("fake"),
    ):
        assert isinstance(geocoder, Geocoder)


def test_nominatim_sends_an_identifying_user_agent_and_no_bulk_request() -> None:
    transport = RecordingTransport(
        [{"lat": "45.46427", "lon": "9.18951", "display_name": "Duomo", "addresstype": "house"}]
    )
    geocoder = adapters.NominatimGeocoder(user_agent="MPMV/1 (ops)", transport=transport)
    [result] = geocoder.geocode(QUERY, language="it", limit=5, timeout=3.0)
    assert result.provider == "nominatim"
    [(url, params, headers, timeout)] = transport.calls
    assert url.startswith("https://nominatim.openstreetmap.org/")
    assert headers["User-Agent"] == "MPMV/1 (ops)"
    assert params["q"] == "Piazza del Duomo 1, 20122 Milano"
    assert params["countrycodes"] == "it"
    assert params["limit"] == "5"
    assert params["accept-language"] == "it"
    assert timeout == 3.0


def test_nominatim_user_agent_comes_from_the_settings(settings: SettingsWrapper) -> None:
    settings.GEOCODING_CONTACT = "geo@example.com"
    settings.WAGTAILADMIN_BASE_URL = "https://shop.example.com"
    geocoder = adapters.NominatimGeocoder(transport=RecordingTransport([]))
    assert "geo@example.com" in geocoder.user_agent
    assert "https://shop.example.com" in geocoder.user_agent


def test_photon_filters_by_country_and_caps_the_limit() -> None:
    transport = RecordingTransport({"features": []})
    geocoder = adapters.PhotonGeocoder(user_agent="MPMV/test", transport=transport)
    assert geocoder.geocode(QUERY, language="it", limit=5, timeout=2.0) == []
    [(url, params, _, timeout)] = transport.calls
    assert url.startswith("https://photon.komoot.io/")
    assert params["q"] == "Piazza del Duomo 1, 20122 Milano"
    assert params["lang"] == "it"
    assert timeout == 2.0


@pytest.mark.parametrize("adapter", [adapters.LocationIQGeocoder, adapters.OpenCageGeocoder])
def test_keyed_adapters_are_off_without_a_key(
    adapter: type[Any], settings: SettingsWrapper
) -> None:
    settings.GEOCODING_LOCATIONIQ_KEY = ""
    settings.GEOCODING_OPENCAGE_KEY = ""
    assert adapter().configured is False
    assert adapter(key=secrets.token_urlsafe(16)).configured is True


@pytest.mark.parametrize("adapter", [adapters.LocationIQGeocoder, adapters.OpenCageGeocoder])
def test_keyed_adapters_read_the_key_from_the_settings(
    adapter: type[Any], settings: SettingsWrapper
) -> None:
    key = secrets.token_urlsafe(16)
    settings.GEOCODING_LOCATIONIQ_KEY = key
    settings.GEOCODING_OPENCAGE_KEY = key
    geocoder = adapter(transport=RecordingTransport({"results": []}))
    assert geocoder.configured is True
    assert key not in repr(geocoder)


def test_locationiq_sends_the_key_only_to_its_own_endpoint() -> None:
    key = secrets.token_urlsafe(16)
    transport = RecordingTransport(
        [{"lat": "45.46427", "lon": "9.18951", "display_name": "Duomo", "type": "house"}]
    )
    [result] = adapters.LocationIQGeocoder(key=key, transport=transport).geocode(
        QUERY, language="en", limit=3, timeout=3.0
    )
    assert result.provider == "locationiq"
    assert result.precision == "rooftop"
    [(url, params, _, _)] = transport.calls
    assert url.startswith("https://")
    assert "locationiq.com" in url
    assert params["key"] == key
    assert params["limit"] == "3"


def test_opencage_parses_its_answer() -> None:
    key = secrets.token_urlsafe(16)
    transport = RecordingTransport(
        {
            "results": [
                {
                    "geometry": {"lat": 45.46427, "lng": 9.18951},
                    "formatted": "Duomo",
                    "confidence": 10,
                    "components": {"_type": "building"},
                }
            ]
        }
    )
    [result] = adapters.OpenCageGeocoder(key=key, transport=transport).geocode(
        QUERY, language="en", limit=5, timeout=3.0
    )
    assert result.provider == "opencage"
    assert result.confidence == 1.0
    [(url, params, _, _)] = transport.calls
    assert "opencagedata.com" in url
    assert params["countrycode"] == "it"


def test_a_malformed_answer_is_an_unavailable_provider() -> None:
    geocoder = adapters.PhotonGeocoder(user_agent="MPMV/test", transport=RecordingTransport([]))
    with pytest.raises(GeocoderUnavailable):
        geocoder.geocode(QUERY, language="en", limit=5, timeout=3.0)


def test_the_fake_returns_its_results_and_records_its_calls() -> None:
    fake = FakeGeocoder("photon", results=[fake_result("photon")])
    assert fake.geocode(QUERY, language="en", limit=5, timeout=3.0)[0].provider == "photon"
    assert fake.calls == [QUERY]
    failing = FakeGeocoder("nominatim", error=GeocoderTimeout("slow"))
    with pytest.raises(GeocoderTimeout):
        failing.geocode(QUERY, language="en", limit=5, timeout=3.0)


# --- HTTP transport (urlopen mocked: never a real request)


class FakeResponse(io.BytesIO):
    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def test_the_http_transport_sends_params_headers_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def fake_urlopen(request: Any, timeout: float) -> FakeResponse:
        seen["url"] = request.full_url
        seen["agent"] = request.get_header("User-agent")
        seen["timeout"] = timeout
        return FakeResponse(b'[{"lat": "1"}]')

    monkeypatch.setattr(adapters.urllib.request, "urlopen", fake_urlopen)
    answer = adapters.http_get_json(
        "https://geo.example.com/search", {"q": "a b"}, {"User-Agent": "MPMV/test"}, 3.0
    )
    assert answer == [{"lat": "1"}]
    assert seen == {
        "url": "https://geo.example.com/search?q=a+b",
        "agent": "MPMV/test",
        "timeout": 3.0,
    }


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError("timed out"), GeocoderTimeout),
        (urllib.error.URLError(TimeoutError("timed out")), GeocoderTimeout),
        (urllib.error.URLError("no route"), GeocoderUnavailable),
        (ConnectionResetError("reset"), GeocoderUnavailable),
    ],
)
def test_the_http_transport_maps_network_errors(
    monkeypatch: pytest.MonkeyPatch, error: Exception, expected: type[Exception]
) -> None:
    def fake_urlopen(request: Any, timeout: float) -> FakeResponse:
        raise error

    monkeypatch.setattr(adapters.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(expected):
        adapters.http_get_json("https://geo.example.com/search", {}, {}, 3.0)


@pytest.mark.parametrize(
    ("status", "expected"),
    [(429, GeocoderRateLimited), (402, GeocoderRateLimited), (500, GeocoderUnavailable)],
)
def test_http_errors_never_carry_the_url_and_its_key(
    monkeypatch: pytest.MonkeyPatch, status: int, expected: type[Exception]
) -> None:
    key = secrets.token_urlsafe(16)
    url = f"https://geo.example.com/search?key={key}"

    def fake_urlopen(request: Any, timeout: float) -> FakeResponse:
        raise urllib.error.HTTPError(url, status, "error", {}, io.BytesIO(b""))  # type: ignore[arg-type]

    monkeypatch.setattr(adapters.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(expected) as caught:
        adapters.http_get_json("https://geo.example.com/search", {"key": key}, {}, 3.0)
    assert key not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True


def test_invalid_json_is_an_unavailable_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        adapters.urllib.request, "urlopen", lambda request, timeout: FakeResponse(b"<html>")
    )
    with pytest.raises(GeocoderUnavailable):
        adapters.http_get_json("https://geo.example.com/search", {}, {}, 3.0)


def test_the_test_settings_refuse_any_network_call() -> None:
    """The default transport in the test settings raises instead of opening a connection."""
    geocoder = adapters.NominatimGeocoder(user_agent="MPMV/test")
    with pytest.raises(GeocoderUnavailable, match="network disabled in tests"):
        geocoder.geocode(QUERY, language="en", limit=5, timeout=3.0)
