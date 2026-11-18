"""The tile cache, the upstream counter and the budget (EVT.14); I/O lives here, rules in `engine`.

Cache: object storage (`settings.MAP_TILE_CACHE_STORAGE`, a Django `STORAGES` alias, the S3
backend on LIVE). Counter and budget: one row per UTC day in the shared `Throttle` table (the
nightly purge forgets old days), so the number of requests sent to the upstream, which the
OpenStreetMap tile usage policy limits, is readable and capped without a new model.
"""

import datetime as dt
from dataclasses import dataclass

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import Storage, storages
from django.utils import timezone

from apps.core.models import Throttle
from apps.core.services import consume_rate_limit

from . import TileBudgetExceeded, TileKey, TileUpstream, engine
from .adapters import default_upstream

COUNTER_KEY = "maptiles:upstream:{day}"
_DAY = dt.timedelta(days=1)


@dataclass(frozen=True)
class Tile:
    body: bytes
    cached: bool
    max_age: int  # seconds a browser or the CDN may keep it


def _storage() -> Storage:
    return storages[settings.MAP_TILE_CACHE_STORAGE]


def user_agent() -> str:
    """The identifying User-Agent: the configured one, else the product with the site URL and the
    operator contact, the same as the geocoder's (no personal data)."""
    from apps.core.geocoding import engine as geo_engine

    return settings.MAP_TILE_USER_AGENT or geo_engine.user_agent(
        settings.WAGTAILADMIN_BASE_URL, settings.GEOCODING_CONTACT
    )


def _day_key(now: dt.datetime) -> str:
    return COUNTER_KEY.format(day=now.astimezone(dt.UTC).date().isoformat())


def upstream_requests(day: dt.date | None = None, *, now: dt.datetime | None = None) -> int:
    """How many requests went to the upstream on `day` (default: today, UTC)."""
    now = now or timezone.now()
    key = COUNTER_KEY.format(day=(day or now.astimezone(dt.UTC).date()).isoformat())
    row = Throttle.objects.filter(key=key).first()
    return row.failures if row else 0


def _count_upstream_request(now: dt.datetime) -> None:
    limit = settings.MAP_TILE_UPSTREAM_DAILY_LIMIT
    if consume_rate_limit(_day_key(now), limit=limit, window=_DAY, now=now):
        raise TileBudgetExceeded


def _cached(path: str, now: dt.datetime) -> tuple[bytes, dt.datetime] | None:
    storage = _storage()
    if not storage.exists(path):
        return None
    with storage.open(path, "rb") as handle:
        unpacked = engine.unpack(handle.read())
    if unpacked is None or unpacked[1] <= now:
        return None
    return unpacked


def get_tile(
    key: TileKey, *, upstream: TileUpstream | None = None, now: dt.datetime | None = None
) -> Tile:
    """The tile: from the cache when fresh, otherwise from the upstream (counted) and then
    cached. An upstream error or a non-PNG answer is raised and never cached."""
    now = now or timezone.now()
    template = settings.MAP_TILE_UPSTREAM_URL
    path = engine.cache_path(template, key)
    browser_max_age = settings.MAP_TILE_BROWSER_MAX_AGE
    if hit := _cached(path, now):
        body, expires = hit
        return Tile(body, True, min(browser_max_age, int((expires - now).total_seconds())))
    _count_upstream_request(now)
    chosen = upstream or default_upstream()
    answer = chosen.fetch(
        engine.upstream_url(template, key),
        user_agent=user_agent(),
        timeout=settings.MAP_TILE_UPSTREAM_TIMEOUT,
    )
    if not engine.is_png(answer.body):
        from . import TileUpstreamError

        raise TileUpstreamError("not a PNG image")
    expires = now + dt.timedelta(seconds=engine.cache_seconds(dict(answer.headers), now))
    storage = _storage()
    if storage.exists(path):
        storage.delete(path)
    storage.save(path, ContentFile(engine.pack(answer.body, expires)))
    return Tile(answer.body, False, browser_max_age)
