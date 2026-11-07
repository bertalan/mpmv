"""Pure geocoding rules (EVT.12): address normalisation, cache key, circuit breaker, limiter
slots and the parsers of the provider answers. No ORM, no I/O."""

import datetime as dt
import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from . import LOCALITY, POSTCODE, REGION, ROOFTOP, STREET, AddressQuery, GeocodeResult

PRODUCT = "MPMV/1.0"
_SPACES = re.compile(r"\s+")
_SIX_PLACES = Decimal("0.000001")

# --- Normalised address and cache key


def normalise(text: str) -> str:
    """Case folded, whitespace collapsed and trimmed."""
    return _SPACES.sub(" ", text).strip().casefold()


def normalised_address(query: AddressQuery) -> str:
    """The cache identity of an address: country, postcode, city and street, normalised."""
    return "|".join(
        normalise(part) for part in (query.country, query.postcode, query.city, query.street)
    )


def cache_key(query: AddressQuery) -> str:
    """SHA-256 of the normalised address (fixed length, indexable)."""
    return hashlib.sha256(normalised_address(query).encode()).hexdigest()


# --- Circuit breaker


@dataclass(frozen=True)
class BreakerState:
    failures: int = 0  # consecutive failures or rate-limit answers
    open_until: dt.datetime | None = None


def breaker_is_open(state: BreakerState, now: dt.datetime) -> bool:
    """Open until `open_until`; then one try is let through (half open)."""
    return state.open_until is not None and now < state.open_until


def breaker_after_failure(
    state: BreakerState, now: dt.datetime, *, threshold: int, cooldown: dt.timedelta
) -> BreakerState:
    """One more consecutive failure; at `threshold` and beyond, the circuit opens again."""
    failures = state.failures + 1
    open_until = now + cooldown if failures >= threshold else state.open_until
    return BreakerState(failures=failures, open_until=open_until)


# --- Limiter


def reserve_slot(
    next_free: dt.datetime | None, now: dt.datetime, *, interval: float
) -> tuple[dt.datetime, dt.datetime]:
    """The instant this request may go out and the next free instant after it."""
    slot = max(now, next_free) if next_free is not None else now
    return slot, slot + dt.timedelta(seconds=interval)


# --- Results


def coordinate(value: Any, *, limit: int = 90) -> Decimal:
    """A latitude (limit 90) or longitude (limit 180) with six decimal places (about 0.1 m)."""
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("not a coordinate") from None
    if not number.is_finite():
        raise ValueError("not a coordinate")
    if abs(number) > limit:
        raise ValueError("coordinate out of range")
    return number.quantize(_SIX_PLACES, rounding=ROUND_HALF_UP)


_PRECISION_BY_KIND = {
    ROOFTOP: {"house", "building", "address", "amenity", "venue", "poi", "attraction"},
    STREET: {"road", "street", "highway", "pedestrian", "residential"},
    POSTCODE: {"postcode", "postal_code"},
    REGION: {"state", "region", "county", "province", "country"},
}


def precision_of(kind: str) -> str:
    """Map an OSM or provider place kind to our precision scale (locality when unknown)."""
    lowered = kind.casefold()
    for precision, kinds in _PRECISION_BY_KIND.items():
        if lowered in kinds:
            return precision
    return LOCALITY


def _result(
    lat: Any, lon: Any, label: str, kind: str, confidence: float | None, provider: str
) -> GeocodeResult:
    return GeocodeResult(
        latitude=coordinate(lat),
        longitude=coordinate(lon, limit=180),
        label=label.strip(),
        precision=precision_of(kind),
        confidence=confidence,
        provider=provider,
    )


def _valid(candidates: Iterable[Any]) -> list[GeocodeResult]:
    return [candidate for candidate in candidates if candidate is not None]


def parse_nominatim(payload: Any, *, provider: str) -> list[GeocodeResult]:
    """Nominatim `jsonv2` (and the LocationIQ answer, which has the same shape)."""
    if not isinstance(payload, list):
        raise ValueError("expected a list of places")

    def one(item: Mapping[str, Any]) -> GeocodeResult | None:
        kind = item.get("addresstype") or item.get("type") or ""
        try:
            return _result(
                item["lat"], item["lon"], str(item.get("display_name", "")), kind, None, provider
            )
        except (KeyError, TypeError, ValueError):
            return None

    return _valid(one(item) for item in payload if isinstance(item, Mapping))


def _photon_label(props: Mapping[str, Any]) -> str:
    street = " ".join(str(props[k]) for k in ("street", "housenumber") if props.get(k))
    place = " ".join(str(props[k]) for k in ("postcode", "city") if props.get(k))
    parts = [str(props.get("name") or ""), street, place]
    return ", ".join(part for part in parts if part)


def parse_photon(payload: Any, *, provider: str, country: str = "") -> list[GeocodeResult]:
    """Photon GeoJSON; Photon has no country filter, so the country is filtered here."""
    if not isinstance(payload, Mapping) or not isinstance(payload.get("features"), list):
        raise ValueError("expected a GeoJSON feature collection")
    wanted = country.strip().casefold()

    def one(feature: Mapping[str, Any]) -> GeocodeResult | None:
        props = feature.get("properties") or {}
        if wanted and str(props.get("countrycode", "")).casefold() != wanted:
            return None
        try:
            lon, lat = feature["geometry"]["coordinates"][:2]
            kind = str(props.get("type", ""))
            return _result(lat, lon, _photon_label(props), kind, None, provider)
        except (KeyError, TypeError, ValueError):
            return None

    return _valid(one(item) for item in payload["features"] if isinstance(item, Mapping))


def parse_opencage(payload: Any, *, provider: str) -> list[GeocodeResult]:
    """OpenCage `json`; its confidence (1 to 10) becomes 0.1 to 1."""
    if not isinstance(payload, Mapping) or not isinstance(payload.get("results"), list):
        raise ValueError("expected a results list")

    def one(item: Mapping[str, Any]) -> GeocodeResult | None:
        raw = item.get("confidence")
        confidence = min(max(float(raw) / 10, 0.0), 1.0) if isinstance(raw, int | float) else None
        kind = str((item.get("components") or {}).get("_type", ""))
        try:
            geometry = item["geometry"]
            return _result(
                geometry["lat"],
                geometry["lng"],
                str(item.get("formatted", "")),
                kind,
                confidence,
                provider,
            )
        except (KeyError, TypeError, ValueError):
            return None

    return _valid(one(item) for item in payload["results"] if isinstance(item, Mapping))


def user_agent(base_url: str, contact: str) -> str:
    """The identifying `User-Agent` required by the Nominatim usage policy."""
    details = "; ".join(part for part in (f"+{base_url}" if base_url else "", contact) if part)
    return f"{PRODUCT} ({details})" if details else PRODUCT
