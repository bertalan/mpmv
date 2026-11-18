"""Map tile proxy (EVT.14, spec 15 "Maps"): same-origin tiles from a configurable upstream.

The browser asks `/maps/tiles/{z}/{x}/{y}.png` on our own domain, so `img-src 'self'` holds and
the visitor's address never reaches a third party. A tile is validated (`engine.parse_tile`),
read from the cache in object storage (`services.get_tile`), and only on a miss fetched from the
upstream, which sits behind the `TileUpstream` protocol: `adapters.HttpTileUpstream` in
production, `fakes.FakeTileUpstream` in tests. The upstream URL template, the attribution, the
User-Agent and every limit are settings (`MAP_TILE_*`), so switching to a paid tile provider is
a configuration change.

Module map: this package holds the types, `engine` the pure rules, `adapters` the HTTP upstream,
`fakes` the test upstream, `services` the cache, counter and limits (I/O), `views` the adapter.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class TileUpstreamError(Exception):
    """The upstream failed; the message names the failure only, never the URL (a key)."""


class TileUpstreamTimeout(TileUpstreamError):
    """The upstream did not answer in time."""


class TileNotAvailable(TileUpstreamError):
    """The upstream has no such tile (HTTP 404)."""


class TileBudgetExceeded(Exception):
    """The daily budget of upstream requests is used up; no further request goes out today."""


class TileBusy(Exception):
    """Another request is fetching this tile and did not finish within the wait."""


@dataclass(frozen=True)
class TileKey:
    z: int
    x: int
    y: int


@dataclass(frozen=True)
class UpstreamTile:
    """What the upstream answered: the image and its caching headers."""

    body: bytes
    headers: Mapping[str, str]


@runtime_checkable
class TileUpstream(Protocol):
    def fetch(self, url: str, *, user_agent: str, timeout: float) -> UpstreamTile:
        """GET one tile; raise a `TileUpstreamError` on any failure."""
        ...
