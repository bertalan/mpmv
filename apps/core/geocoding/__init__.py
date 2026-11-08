"""Server-side geocoding behind our own interface (EVT.12, spec 15 "Location, coordinates and
geocoding").

Every provider is an adapter that follows the `Geocoder` protocol; `geocode` runs the configured
chain (`settings.GEOCODING_PROVIDERS`) with the normalised-address cache, the PostgreSQL-backed
per-provider limiter, the per-call timeout, the chain budget and a circuit breaker per provider
(`services.geocode_address`). Only a person triggers a lookup, through the "Find coordinates"
endpoint (`views.find_coordinates`): never on save, in a loop, a migration or an import job.

Module map: this package holds the types, `engine` the pure rules, `adapters` the providers and
the HTTP transport, `fakes` the `FakeGeocoder` used by tests, `services` the chain (ORM).
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, NamedTuple, Protocol, runtime_checkable

# Precision of a candidate, from the most to the least exact.
ROOFTOP = "rooftop"
STREET = "street"
POSTCODE = "postcode"
LOCALITY = "locality"
REGION = "region"
PRECISIONS = (ROOFTOP, STREET, POSTCODE, LOCALITY, REGION)

# What happened to each provider of the chain during one lookup.
OK = "ok"
EMPTY = "empty"
FAILED = "failed"
TIMEOUT = "timeout"
RATE_LIMITED = "rate_limited"  # the provider answered "too many requests" or "quota exceeded"
THROTTLED = "throttled"  # our own limiter could not give a slot within the chain budget
CIRCUIT_OPEN = "circuit_open"
NO_KEY = "no_key"
BUDGET_EXHAUSTED = "budget_exhausted"


@dataclass(frozen=True)
class AddressQuery:
    """The venue address typed in a form: nothing else is ever sent to a provider."""

    country: str = ""  # ISO 3166-1 alpha-2
    postcode: str = ""
    city: str = ""
    street: str = ""

    def is_empty(self) -> bool:
        """A country alone is not an address."""
        return not (self.postcode.strip() or self.city.strip() or self.street.strip())

    def text(self) -> str:
        """The free-text query: "street, postcode city" (the country goes in a filter)."""
        place = " ".join(part.strip() for part in (self.postcode, self.city) if part.strip())
        return ", ".join(part for part in (self.street.strip(), place) if part)


@dataclass(frozen=True)
class GeocodeResult:
    latitude: Decimal
    longitude: Decimal
    label: str
    precision: str  # one of PRECISIONS
    confidence: float | None  # 0 to 1 when the provider gives one
    provider: str

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly form, for the cache and the endpoint (coordinates as strings)."""
        return {
            "latitude": str(self.latitude),
            "longitude": str(self.longitude),
            "label": self.label,
            "precision": self.precision,
            "confidence": self.confidence,
            "provider": self.provider,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GeocodeResult":
        confidence = data.get("confidence")
        return cls(
            latitude=Decimal(str(data["latitude"])),
            longitude=Decimal(str(data["longitude"])),
            label=str(data["label"]),
            precision=str(data["precision"]),
            confidence=None if confidence is None else float(confidence),
            provider=str(data["provider"]),
        )


class Attempt(NamedTuple):
    provider: str
    status: str  # OK, EMPTY, FAILED, ...


@dataclass(frozen=True)
class GeocodeOutcome:
    """The candidates of one lookup, the provider that gave them and every provider tried."""

    results: tuple[GeocodeResult, ...]
    provider: str | None
    attempts: tuple[Attempt, ...]
    cached: bool = False


@runtime_checkable
class Geocoder(Protocol):
    """One provider. Adding a provider is a new adapter and a settings entry."""

    name: str

    @property
    def configured(self) -> bool:
        """False when the adapter cannot be used (e.g. no API key): the chain skips it."""
        ...

    def geocode(
        self, query: AddressQuery, *, language: str, limit: int = 5, timeout: float = 3.0
    ) -> list[GeocodeResult]:
        """Up to `limit` candidates; raise a `GeocoderError` when the provider fails."""
        ...


class GeocoderError(Exception):
    """A provider could not answer. Messages never carry URLs, keys or the address."""


class GeocoderUnavailable(GeocoderError):
    """Network error, server error or an answer we cannot read."""


class GeocoderTimeout(GeocoderError):
    """The provider did not answer within the timeout."""


class GeocoderRateLimited(GeocoderError):
    """The provider refused the request because of its rate limit or quota."""


def geocode(query: AddressQuery, *, language: str) -> GeocodeOutcome:
    """Look `query` up through the configured chain and cache (see `services.geocode_address`)."""
    from .services import geocode_address

    return geocode_address(query, language=language)
