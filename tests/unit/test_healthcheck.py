"""Container health probe used by Podman and the local stack (ARC.5)."""

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
