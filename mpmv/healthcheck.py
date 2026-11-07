"""Container health probe: `python -m mpmv.healthcheck` exits 0 when /healthz answers 200.

Used by the Podman Quadlet `HealthCmd` and by the local stack. It runs inside the app container,
so it calls the app server on the loopback interface with a Host header Django accepts, and marks
the request as HTTPS so LIVE settings do not redirect it.
"""

import os
import sys
import urllib.error
import urllib.request

from mpmv.settings.env import env_list


def probe_host(allowed_hosts: list[str]) -> str:
    """Pick a Host header the app accepts: the first concrete entry of ALLOWED_HOSTS."""
    for host in allowed_hosts:
        if host != "*":
            return host.lstrip(".")
    return "localhost"


def probe(url: str, host: str, timeout: float = 5.0) -> bool:
    """Return True when `url` answers 200 without a redirect."""
    request = urllib.request.Request(  # noqa: S310 (fixed http URL on the loopback interface)
        url, headers={"Host": host, "X-Forwarded-Proto": "https"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return bool(response.status == 200)
    except (urllib.error.URLError, OSError):
        return False


def main() -> int:
    port = os.environ.get("MPMV_HTTP_PORT", "8000")
    host = probe_host(env_list("DJANGO_ALLOWED_HOSTS"))
    return 0 if probe(f"http://127.0.0.1:{port}/healthz", host) else 1


if __name__ == "__main__":
    sys.exit(main())
