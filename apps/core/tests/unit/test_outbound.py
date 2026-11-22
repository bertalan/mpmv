"""SEC.5 follow-up: outbound HTTP never follows a redirect, at the socket level."""

import logging
import secrets
import urllib.error
import urllib.request

import pytest

from apps.core.geocoding import GeocoderUnavailable
from apps.core.geocoding.adapters import http_get_json
from apps.core.maps import TileUpstreamError
from apps.core.maps.adapters import HttpTileUpstream
from apps.core.outbound import RedirectRefused, open_url
from apps.core.tests.redirect_server import redirect_server


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_open_url_refuses_every_redirect_to_another_host(status: int) -> None:
    with redirect_server(status) as server:
        request = urllib.request.Request(f"{server.base_url}/start", method="GET")  # noqa: S310 (local test server)
        with pytest.raises(RedirectRefused) as refused:
            open_url(request, timeout=5)
    assert refused.value.code == status
    assert isinstance(refused.value, urllib.error.HTTPError)
    assert server.paths == ["/start"]


def test_the_log_names_status_and_host_but_never_the_query(
    caplog: pytest.LogCaptureFixture,
) -> None:
    token = secrets.token_urlsafe(12)
    with redirect_server() as server, caplog.at_level(logging.WARNING, "apps.core.outbound"):
        request = urllib.request.Request(f"{server.base_url}/q?key={token}", method="GET")  # noqa: S310 (local test server)
        with pytest.raises(RedirectRefused) as refused:
            open_url(request, timeout=5)
    assert "302" in caplog.text
    assert "127.0.0.1" in caplog.text
    assert token not in caplog.text
    assert "leak" not in caplog.text
    assert token not in str(refused.value)
    assert token not in refused.value.geturl()


def test_geocoding_transport_refuses_a_redirect() -> None:
    with redirect_server() as server, pytest.raises(GeocoderUnavailable, match="HTTP 302"):
        http_get_json(f"{server.base_url}/search", {"q": "x"}, {}, 5)
    assert server.paths == ["/search?q=x"]


def test_tile_upstream_refuses_a_redirect() -> None:
    with redirect_server() as server, pytest.raises(TileUpstreamError, match="HTTP 302"):
        HttpTileUpstream().fetch(f"{server.base_url}/1.png", user_agent="x", timeout=5)
    assert server.paths == ["/1.png"]
