"""Container health probe used by Podman and the local stack (ARC.5)."""

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from mpmv.healthcheck import probe, probe_host


@pytest.mark.parametrize(
    ("allowed_hosts", "expected"),
    [
        (["shop.example.org", "www.example.org"], "shop.example.org"),
        ([".example.org"], "example.org"),
        (["*"], "localhost"),
        ([], "localhost"),
    ],
)
def test_probe_host_uses_the_first_concrete_allowed_host(
    allowed_hosts: list[str], expected: str
) -> None:
    assert probe_host(allowed_hosts) == expected


def test_probe_reports_an_unreachable_server_as_unhealthy() -> None:
    # Port 9 (discard) on the loopback interface: nothing listens there in CI or locally.
    assert probe("http://127.0.0.1:9/healthz", host="localhost", timeout=1) is False


class _Recorder(BaseHTTPRequestHandler):
    headers_seen: list[dict[str, str]] = []

    def do_GET(self) -> None:
        type(self).headers_seen.append(dict(self.headers))
        self.send_response(200)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture
def recorder() -> Iterator[HTTPServer]:
    _Recorder.headers_seen = []
    server = HTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def test_probe_sends_plain_http_without_a_forged_scheme(recorder: HTTPServer) -> None:
    # LIVE exempts /healthz from the HTTPS redirect (SEC.1), so the probe must not pretend to come
    # through the TLS proxy: only the proxy sets X-Forwarded-Proto.
    port = recorder.server_address[1]
    assert probe(f"http://127.0.0.1:{port}/healthz", host="shop.example.org") is True
    (headers,) = _Recorder.headers_seen
    assert headers["Host"] == "shop.example.org"
    assert "x-forwarded-proto" not in {name.lower() for name in headers}
