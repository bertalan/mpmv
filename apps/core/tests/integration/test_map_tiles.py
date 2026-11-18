"""EVT.14: the tile proxy view with a fake upstream; no test reaches the network."""

import datetime as dt
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from django.core.cache import cache
from django.db import connection
from django.test import Client
from django.urls import reverse

from apps.core.maps import TileUpstreamError, TileUpstreamTimeout, adapters, engine, services
from apps.core.maps.fakes import FakeTileUpstream
from apps.core.models import Throttle

pytestmark = pytest.mark.django_db

URL = "/maps/tiles/{z}/{x}/{y}.png"


@pytest.fixture
def upstream(monkeypatch: pytest.MonkeyPatch, settings: Any, tmp_path: Path) -> FakeTileUpstream:
    settings.MEDIA_ROOT = tmp_path
    settings.STORAGES = {
        **settings.STORAGES,
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": str(tmp_path)},
        },
    }
    cache.clear()
    fake = FakeTileUpstream()
    monkeypatch.setattr(services, "default_upstream", lambda: fake)
    return fake


def _get(client: Client, z: object = 3, x: object = 4, y: object = 5, **extra: Any) -> Any:
    return client.get(f"/maps/tiles/{z}/{x}/{y}.png", **extra)


def test_the_route_is_outside_the_language_prefix() -> None:
    assert reverse("map_tile", kwargs={"z": "3", "x": "4", "y": "5"}) == "/maps/tiles/3/4/5.png"


def test_a_tile_is_fetched_once_and_then_served_from_the_cache(
    client: Client, upstream: FakeTileUpstream
) -> None:
    first = _get(client)
    second = _get(client)
    assert first.status_code == second.status_code == 200
    assert first["Content-Type"] == "image/png"
    assert first.content == second.content == upstream.body
    assert first["X-Tile-Cache"] == "miss"
    assert second["X-Tile-Cache"] == "hit"
    assert len(upstream.calls) == 1


def test_other_tiles_are_other_upstream_calls(client: Client, upstream: FakeTileUpstream) -> None:
    _get(client, 3, 4, 5)
    _get(client, 3, 4, 6)
    assert [call[0] for call in upstream.calls] == [
        "https://tile.openstreetmap.org/3/4/5.png",
        "https://tile.openstreetmap.org/3/4/6.png",
    ]


@pytest.mark.parametrize(
    ("z", "x", "y"),
    [
        (19, 0, 0),
        (1, 0, 0),
        (0, 0, 0),
        (-1, 0, 0),
        (3, -1, 0),
        (3, 8, 0),
        ("a", 0, 0),
        (3, "1.5", 0),
        (3, "0x1", 0),
    ],
)
def test_invalid_tiles_are_404_without_an_upstream_call(
    client: Client, upstream: FakeTileUpstream, z: object, x: object, y: object
) -> None:
    response = _get(client, z, x, y)
    assert response.status_code == 404
    assert response["Cache-Control"] == "no-store"
    assert upstream.calls == []
    assert Throttle.objects.count() == 0  # not even the IP limiter was touched


def test_only_get_is_allowed(client: Client, upstream: FakeTileUpstream) -> None:
    assert client.post("/maps/tiles/3/4/5.png").status_code == 405
    assert upstream.calls == []


def test_long_cache_headers_for_the_cdn(client: Client, upstream: FakeTileUpstream) -> None:
    response = _get(client)
    assert response["Cache-Control"] == "public, max-age=604800"
    assert _get(client)["Cache-Control"].startswith("public, max-age=6")  # a hit: time left


def test_the_upstream_gets_an_identifying_generic_user_agent(
    client: Client, upstream: FakeTileUpstream, settings: Any
) -> None:
    settings.WAGTAILADMIN_BASE_URL = "https://shop.example.eu"
    settings.GEOCODING_CONTACT = "ops@example.eu"
    _get(client)
    agent = upstream.calls[0][1]
    assert "https://shop.example.eu" in agent
    assert agent.startswith("MPMV")


def test_the_user_agent_can_be_configured(
    client: Client, upstream: FakeTileUpstream, settings: Any
) -> None:
    settings.MAP_TILE_USER_AGENT = "MPMV-tiles/1.0"
    _get(client)
    assert upstream.calls[0][1] == "MPMV-tiles/1.0"


def test_switching_the_upstream_setting_needs_no_code_and_never_reuses_old_tiles(
    client: Client, upstream: FakeTileUpstream, settings: Any
) -> None:
    _get(client)
    settings.MAP_TILE_UPSTREAM_URL = "https://eu-tiles.example.net/v2/{z}/{x}/{y}.png"
    _get(client)
    assert [call[0] for call in upstream.calls] == [
        "https://tile.openstreetmap.org/3/4/5.png",
        "https://eu-tiles.example.net/v2/3/4/5.png",
    ]


def test_the_upstream_counter_counts_only_upstream_calls(
    client: Client, upstream: FakeTileUpstream
) -> None:
    assert services.upstream_requests() == 0
    _get(client)
    _get(client)  # a cache hit
    _get(client, 3, 4, 6)
    assert services.upstream_requests() == 2 == len(upstream.calls)
    _get(client, 19, 0, 0)  # invalid
    assert services.upstream_requests() == 2


def test_the_daily_budget_stops_upstream_requests(
    client: Client, upstream: FakeTileUpstream, settings: Any
) -> None:
    settings.MAP_TILE_UPSTREAM_DAILY_LIMIT = 1
    assert _get(client, 3, 4, 5).status_code == 200
    blocked = _get(client, 3, 4, 6)
    assert blocked.status_code == 503
    assert blocked["Retry-After"]
    assert _get(client, 3, 4, 5).status_code == 200  # still served from the cache
    assert len(upstream.calls) == 1


def test_a_per_ip_rate_limit_answers_429(
    client: Client, upstream: FakeTileUpstream, settings: Any
) -> None:
    settings.MAP_TILE_IP_LIMIT = 2
    assert _get(client).status_code == 200
    assert _get(client).status_code == 200
    third = _get(client)
    assert third.status_code == 429
    assert int(third["Retry-After"]) >= 1
    assert third["Cache-Control"] == "no-store"
    assert _get(client, REMOTE_ADDR="203.0.113.9").status_code == 200  # another client


def test_the_limit_uses_the_trusted_proxy_client_address(
    client: Client, upstream: FakeTileUpstream, settings: Any
) -> None:
    settings.MAP_TILE_IP_LIMIT = 1
    settings.TRUSTED_PROXIES = ["127.0.0.1"]
    settings.TRUSTED_PROXY_HEADER = "X-Real-IP"
    assert _get(client, REMOTE_ADDR="127.0.0.1", HTTP_X_REAL_IP="198.51.100.1").status_code == 200
    assert _get(client, REMOTE_ADDR="127.0.0.1", HTTP_X_REAL_IP="198.51.100.1").status_code == 429
    assert _get(client, REMOTE_ADDR="127.0.0.1", HTTP_X_REAL_IP="198.51.100.2").status_code == 200


@pytest.mark.parametrize(
    ("error", "status"),
    [(TileUpstreamError("HTTP 500"), 502), (TileUpstreamTimeout("timed out"), 504)],
)
def test_an_upstream_error_is_not_cached(
    client: Client, upstream: FakeTileUpstream, error: TileUpstreamError, status: int
) -> None:
    upstream.error = error
    response = _get(client)
    assert response.status_code == status
    assert response["Cache-Control"] == "no-store"
    upstream.error = None
    assert _get(client).status_code == 200  # the next request asks the upstream again
    assert len(upstream.calls) == 2


def test_an_answer_that_is_not_a_png_is_a_502_and_not_cached(
    client: Client, upstream: FakeTileUpstream
) -> None:
    upstream.body = b"<html>rate limited</html>"
    assert _get(client).status_code == 502
    upstream.body = engine.PNG_MAGIC + b"ok"
    assert _get(client).status_code == 200


def test_an_expired_tile_is_fetched_again(upstream: FakeTileUpstream, settings: Any) -> None:
    from apps.core.maps import TileKey

    start = dt.datetime(2027, 3, 1, 12, tzinfo=dt.UTC)
    key = TileKey(3, 4, 5)
    services.get_tile(key, upstream=upstream, now=start)
    assert services.get_tile(key, upstream=upstream, now=start + dt.timedelta(days=6)).cached
    again = services.get_tile(key, upstream=upstream, now=start + dt.timedelta(days=8))
    assert not again.cached
    assert len(upstream.calls) == 2


def test_a_longer_upstream_lifetime_is_honoured(upstream: FakeTileUpstream) -> None:
    from apps.core.maps import TileKey

    upstream.headers = {"Cache-Control": "max-age=2592000"}
    start = dt.datetime(2027, 3, 1, 12, tzinfo=dt.UTC)
    key = TileKey(3, 4, 5)
    services.get_tile(key, upstream=upstream, now=start)
    assert services.get_tile(key, upstream=upstream, now=start + dt.timedelta(days=20)).cached


def test_a_damaged_cache_entry_is_a_miss(upstream: FakeTileUpstream, settings: Any) -> None:
    from django.core.files.base import ContentFile

    from apps.core.maps import TileKey

    key = TileKey(3, 4, 5)
    path = engine.cache_path(settings.MAP_TILE_UPSTREAM_URL, key)
    services._storage().save(path, ContentFile(b"garbage"))
    assert not services.get_tile(key, upstream=upstream).cached


def test_the_test_settings_refuse_the_network() -> None:
    with pytest.raises(TileUpstreamError):
        adapters.RefuseNetworkUpstream().fetch("https://x.example/1", user_agent="a", timeout=1)


def test_tile_responses_carry_no_cookie_and_no_language_vary(
    client: Client, upstream: FakeTileUpstream
) -> None:
    for _ in range(2):  # a miss, then a hit
        response = _get(client, HTTP_ACCEPT_LANGUAGE="it")
        assert response.status_code == 200
        assert not response.cookies
        assert "Set-Cookie" not in response
        vary = {v.strip().lower() for v in response.get("Vary", "").split(",")}
        assert "accept-language" not in vary
        assert "cookie" not in vary


def test_a_redirect_from_the_upstream_is_an_error_and_not_cached(monkeypatch: Any) -> None:
    import io
    import urllib.request
    import urllib.response
    from email.message import Message

    from apps.core.maps import TileUpstreamError

    class Handler(urllib.request.BaseHandler):
        handler_order = 100  # before the real HTTPS handler
        calls: list[str] = []

        def https_open(self, req: Any) -> Any:
            self.calls.append(req.full_url)
            if len(self.calls) == 1:
                headers = Message()
                headers["Location"] = "http://elsewhere.invalid/t.png"
                reply = urllib.response.addinfourl(io.BytesIO(b""), headers, req.full_url, 302)
                reply.msg = "Found"  # type: ignore[attr-defined]
                return reply
            raise AssertionError("the redirect was followed")

    handler = Handler()
    monkeypatch.setattr(adapters, "_extra_handlers", lambda: [handler])
    with pytest.raises(TileUpstreamError):
        adapters.HttpTileUpstream().fetch("https://t.invalid/1.png", user_agent="x", timeout=1)
    assert handler.calls == ["https://t.invalid/1.png"]


# --- single-flight for a cold tile (EVT.14 follow-up) ---


class _SlowUpstream(FakeTileUpstream):
    """Holds every fetch for `delay` seconds, so concurrent requests overlap."""

    def __init__(self, delay: float, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.delay = delay

    def fetch(self, url: str, *, user_agent: str, timeout: float) -> Any:
        time.sleep(self.delay)
        return super().fetch(url, user_agent=user_agent, timeout=timeout)


def _burst(coordinates: list[tuple[int, int, int]]) -> list[Any]:
    """One request per entry, all started together, each thread on its own DB connection."""
    barrier = threading.Barrier(len(coordinates))

    def one(coordinate: tuple[int, int, int]) -> Any:
        try:
            barrier.wait(timeout=10)
            return _get(Client(), *coordinate)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=len(coordinates)) as pool:
        return list(pool.map(one, coordinates))


@pytest.fixture
def slow_upstream(
    monkeypatch: pytest.MonkeyPatch, settings: Any, tmp_path: Path
) -> Iterator[_SlowUpstream]:
    settings.STORAGES = {
        **settings.STORAGES,
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": str(tmp_path)},
        },
    }
    cache.clear()
    fake = _SlowUpstream(0.6)
    monkeypatch.setattr(services, "default_upstream", lambda: fake)
    yield fake
    cache.clear()


@pytest.mark.django_db(transaction=True)
def test_a_burst_for_one_cold_tile_makes_one_upstream_call(slow_upstream: _SlowUpstream) -> None:
    responses = _burst([(3, 4, 5)] * 6)
    assert [r.status_code for r in responses] == [200] * 6
    assert {r.content for r in responses} == {slow_upstream.body}
    assert len(slow_upstream.calls) == 1
    assert sorted(r["X-Tile-Cache"] for r in responses) == ["hit"] * 5 + ["miss"]
    assert services.upstream_requests() == 1


@pytest.mark.django_db(transaction=True)
def test_different_tiles_do_not_wait_for_each_other(slow_upstream: _SlowUpstream) -> None:
    started = time.monotonic()
    responses = _burst([(3, 4, 5), (3, 4, 6), (3, 5, 5), (3, 5, 6)])
    elapsed = time.monotonic() - started
    assert [r.status_code for r in responses] == [200] * 4
    assert len(slow_upstream.calls) == 4
    assert elapsed < 4 * slow_upstream.delay  # in parallel, not one after the other


@pytest.mark.django_db(transaction=True)
def test_waiters_get_503_when_the_upstream_is_slower_than_the_wait(
    slow_upstream: _SlowUpstream, settings: Any
) -> None:
    settings.MAP_TILE_SINGLE_FLIGHT_WAIT = 0.2
    slow_upstream.delay = 1.2
    responses = _burst([(3, 4, 5)] * 4)
    by_status = sorted(r.status_code for r in responses)
    assert by_status == [200, 503, 503, 503]
    for busy in (r for r in responses if r.status_code == 503):
        assert busy["Retry-After"] == "2"
        assert busy["Cache-Control"] == "no-store"
    assert len(slow_upstream.calls) == 1
    assert services.upstream_requests() == 1
    assert _get(Client()).status_code == 200  # the tile is cached afterwards
    assert len(slow_upstream.calls) == 1


@pytest.mark.django_db(transaction=True)
def test_the_lock_is_released_after_a_failed_fetch(slow_upstream: _SlowUpstream) -> None:
    slow_upstream.error = TileUpstreamError("boom")
    slow_upstream.delay = 0
    assert _get(Client()).status_code == 502
    slow_upstream.error = None
    assert _get(Client()).status_code == 200
    assert len(slow_upstream.calls) == 2
