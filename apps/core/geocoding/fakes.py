"""`FakeGeocoder`: a provider that answers from memory, for tests and local demos (EVT.12).

No test calls a real provider: tests build the chain from fakes, or pass a recording transport
to a real adapter.
"""

import datetime as dt
from collections.abc import Callable, Sequence
from decimal import Decimal

from . import ROOFTOP, AddressQuery, GeocoderError, GeocodeResult


def fake_result(
    provider: str,
    *,
    latitude: str = "45.464270",
    longitude: str = "9.189510",
    label: str = "Piazza del Duomo 1, 20122 Milano",
    precision: str = ROOFTOP,
    confidence: float | None = None,
) -> GeocodeResult:
    return GeocodeResult(
        latitude=Decimal(latitude),
        longitude=Decimal(longitude),
        label=label,
        precision=precision,
        confidence=confidence,
        provider=provider,
    )


class FakeGeocoder:
    """Returns `results` (or raises `error`) and records every call it gets."""

    def __init__(
        self,
        name: str,
        *,
        results: Sequence[GeocodeResult] = (),
        error: GeocoderError | None = None,
        configured: bool = True,
        clock: Callable[[], dt.datetime] | None = None,
    ) -> None:
        self.name = name
        self.results = list(results)
        self.error = error
        self._configured = configured
        self._clock = clock
        self.calls: list[AddressQuery] = []
        self.timeouts: list[float] = []
        self.call_times: list[dt.datetime] = []

    @property
    def configured(self) -> bool:
        return self._configured

    def __repr__(self) -> str:
        return f"<FakeGeocoder {self.name}>"

    def geocode(
        self, query: AddressQuery, *, language: str, limit: int = 5, timeout: float = 3.0
    ) -> list[GeocodeResult]:
        self.calls.append(query)
        self.timeouts.append(timeout)
        if self._clock is not None:
            self.call_times.append(self._clock())
        if self.error is not None:
            raise self.error
        return self.results[:limit]
