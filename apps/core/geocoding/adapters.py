"""Provider adapters (EVT.12): Nominatim, Photon, LocationIQ and OpenCage behind `Geocoder`.

Each adapter builds one request and hands it to a transport: `http_get_json` in production,
whatever `settings.GEOCODING_TRANSPORT` names otherwise (the test settings name one that
refuses), or the `transport` given to the constructor. Keyed adapters read their key from the
settings (filled from the environment) and are off without one; no key is ever logged, shown
in a `repr` or carried by an exception message.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

from django.conf import settings
from django.utils.module_loading import import_string

from . import (
    AddressQuery,
    GeocodeResult,
    GeocoderRateLimited,
    GeocoderTimeout,
    GeocoderUnavailable,
    engine,
)

Transport = Callable[[str, Mapping[str, str], Mapping[str, str], float], Any]

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
PHOTON_URL = "https://photon.komoot.io/api/"
LOCATIONIQ_URL = "https://eu1.locationiq.com/v1/search"
OPENCAGE_URL = "https://api.opencagedata.com/geocode/v1/json"

# Languages Photon's public instance answers in; any other one gets its default names.
PHOTON_LANGUAGES = frozenset({"default", "en", "de", "fr", "it"})
_RATE_LIMIT_STATUSES = frozenset({402, 429})  # 402: OpenCage quota exceeded


def _is_timeout(error: BaseException) -> bool:
    return isinstance(error, TimeoutError) or (
        isinstance(error, urllib.error.URLError) and isinstance(error.reason, TimeoutError)
    )


def http_get_json(
    url: str, params: Mapping[str, str], headers: Mapping[str, str], timeout: float
) -> Any:
    """GET `url` with `params` and return the decoded JSON.

    Errors become `GeocoderError`s whose message names only the failure, never the URL (it may
    carry a key); the original exception is dropped from the chain for the same reason.
    """
    full_url = f"{url}?{urllib.parse.urlencode(params)}" if params else url
    request = urllib.request.Request(full_url, headers=dict(headers), method="GET")  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            body = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code in _RATE_LIMIT_STATUSES:
            raise GeocoderRateLimited(f"HTTP {exc.code}") from None
        raise GeocoderUnavailable(f"HTTP {exc.code}") from None
    except OSError as exc:  # URLError, connection errors, timeouts
        if _is_timeout(exc):
            raise GeocoderTimeout("timed out") from None
        raise GeocoderUnavailable(type(exc).__name__) from None
    try:
        return json.loads(body)
    except ValueError:
        raise GeocoderUnavailable("invalid JSON") from None


def refuse_network(
    url: str, params: Mapping[str, str], headers: Mapping[str, str], timeout: float
) -> Any:
    """The transport of the test settings: no test may reach a real provider."""
    raise GeocoderUnavailable("network disabled in tests: use FakeGeocoder")


def _default_transport() -> Transport:
    transport: Transport = import_string(settings.GEOCODING_TRANSPORT)
    return transport


def _parsed[T](parse: Callable[[], T]) -> T:
    try:
        return parse()
    except ValueError:
        raise GeocoderUnavailable("unexpected answer") from None


def _platform_user_agent() -> str:
    return engine.user_agent(settings.WAGTAILADMIN_BASE_URL, settings.GEOCODING_CONTACT)


class _Adapter:
    name = ""
    url = ""

    def __init__(self, *, url: str | None = None, transport: Transport | None = None) -> None:
        self.url = url or self.url
        self._transport = transport or _default_transport()

    @property
    def configured(self) -> bool:
        return True

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name} configured={self.configured}>"

    def _get(self, params: Mapping[str, str], headers: Mapping[str, str], timeout: float) -> Any:
        return self._transport(self.url, params, headers, timeout)


class NominatimGeocoder(_Adapter):
    """OpenStreetMap Nominatim: at most 1 request per second for the whole platform (the chain's
    limiter), identifying User-Agent, single lookups only, results cached."""

    name = "nominatim"
    url = NOMINATIM_URL

    def __init__(
        self,
        *,
        user_agent: str | None = None,
        url: str | None = None,
        transport: Transport | None = None,
    ) -> None:
        super().__init__(url=url, transport=transport)
        self.user_agent = user_agent or _platform_user_agent()

    def geocode(
        self, query: AddressQuery, *, language: str, limit: int = 5, timeout: float = 3.0
    ) -> list[GeocodeResult]:
        params = {
            "q": query.text(),
            "format": "jsonv2",
            "limit": str(limit),
            "accept-language": language,
        }
        if query.country.strip():
            params["countrycodes"] = query.country.strip().lower()
        payload = self._get(params, {"User-Agent": self.user_agent}, timeout)
        return _parsed(lambda: engine.parse_nominatim(payload, provider=self.name)[:limit])


class PhotonGeocoder(_Adapter):
    """Photon (komoot), OSM-based; the country is filtered on our side."""

    name = "photon"
    url = PHOTON_URL

    def __init__(
        self,
        *,
        user_agent: str | None = None,
        url: str | None = None,
        transport: Transport | None = None,
    ) -> None:
        super().__init__(url=url, transport=transport)
        self.user_agent = user_agent or _platform_user_agent()

    def geocode(
        self, query: AddressQuery, *, language: str, limit: int = 5, timeout: float = 3.0
    ) -> list[GeocodeResult]:
        params = {"q": query.text(), "limit": str(limit)}
        if language in PHOTON_LANGUAGES:
            params["lang"] = language
        payload = self._get(params, {"User-Agent": self.user_agent}, timeout)
        return _parsed(
            lambda: engine.parse_photon(payload, provider=self.name, country=query.country)[:limit]
        )


class _KeyedAdapter(_Adapter):
    key_setting = ""

    def __init__(
        self, *, key: str | None = None, url: str | None = None, transport: Transport | None = None
    ) -> None:
        super().__init__(url=url, transport=transport)
        self.__key = key if key is not None else str(getattr(settings, self.key_setting, ""))

    @property
    def configured(self) -> bool:
        return bool(self.__key)

    @property
    def _key(self) -> str:
        return self.__key


class LocationIQGeocoder(_KeyedAdapter):
    """LocationIQ free tier, only with `MPMV_GEOCODING_LOCATIONIQ_KEY`."""

    name = "locationiq"
    url = LOCATIONIQ_URL
    key_setting = "GEOCODING_LOCATIONIQ_KEY"

    def geocode(
        self, query: AddressQuery, *, language: str, limit: int = 5, timeout: float = 3.0
    ) -> list[GeocodeResult]:
        params = {
            "key": self._key,
            "q": query.text(),
            "format": "json",
            "limit": str(limit),
            "accept-language": language,
        }
        if query.country.strip():
            params["countrycodes"] = query.country.strip().lower()
        payload = self._get(params, {}, timeout)
        return _parsed(lambda: engine.parse_nominatim(payload, provider=self.name)[:limit])


class OpenCageGeocoder(_KeyedAdapter):
    """OpenCage free tier, only with `MPMV_GEOCODING_OPENCAGE_KEY`."""

    name = "opencage"
    url = OPENCAGE_URL
    key_setting = "GEOCODING_OPENCAGE_KEY"

    def geocode(
        self, query: AddressQuery, *, language: str, limit: int = 5, timeout: float = 3.0
    ) -> list[GeocodeResult]:
        params = {
            "key": self._key,
            "q": query.text(),
            "limit": str(limit),
            "language": language,
            "no_annotations": "1",
        }
        if query.country.strip():
            params["countrycode"] = query.country.strip().lower()
        payload = self._get(params, {}, timeout)
        return _parsed(lambda: engine.parse_opencage(payload, provider=self.name)[:limit])
