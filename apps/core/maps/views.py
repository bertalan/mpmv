"""`/maps/tiles/{z}/{x}/{y}.png` (EVT.14): a thin adapter over `services.get_tile`.

Public and anonymous by design: a tile is the same for everyone and carries no user data. The
path is validated first (a bad value is a 404 before any database or upstream call), then the
per-IP limit, then the cache and the upstream.
"""

import datetime as dt
import math

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.views.decorators.http import require_GET

from apps.core.http import client_ip
from apps.core.services import consume_rate_limit

from . import (
    TileBudgetExceeded,
    TileBusy,
    TileNotAvailable,
    TileUpstreamError,
    TileUpstreamTimeout,
    engine,
    services,
)

NO_STORE = "no-store"


def _refusal(status: int, text: str, *, retry_after: int | None = None) -> HttpResponse:
    response = HttpResponse(text, status=status, content_type="text/plain; charset=utf-8")
    response["Cache-Control"] = NO_STORE
    if retry_after is not None:
        response["Retry-After"] = str(retry_after)
    return response


@require_GET
def tile(request: HttpRequest, z: str, x: str, y: str) -> HttpResponse:
    key = engine.parse_tile(
        z, x, y, min_zoom=settings.MAP_TILE_MIN_ZOOM, max_zoom=settings.MAP_TILE_MAX_ZOOM
    )
    if key is None:
        return _refusal(404, "Not found")
    wait = consume_rate_limit(
        f"maptiles:ip:{client_ip(request) or 'unknown'}",
        limit=settings.MAP_TILE_IP_LIMIT,
        window=dt.timedelta(seconds=settings.MAP_TILE_IP_WINDOW),
    )
    if wait:
        seconds = max(1, math.ceil(wait.total_seconds()))
        return _refusal(429, "Too many requests", retry_after=seconds)
    try:
        result = services.get_tile(key)
    except TileNotAvailable:
        return _refusal(404, "Not found")
    except TileBudgetExceeded:
        return _refusal(503, "Tile service busy", retry_after=3600)
    except TileBusy:
        return _refusal(503, "Tile is being fetched, retry", retry_after=2)
    except TileUpstreamTimeout:
        return _refusal(504, "Tile server timed out")
    except TileUpstreamError:
        return _refusal(502, "Tile server unavailable")
    response = HttpResponse(result.body, content_type="image/png")
    response["Cache-Control"] = engine.cache_control(result.max_age)
    response["X-Tile-Cache"] = "hit" if result.cached else "miss"
    return response
