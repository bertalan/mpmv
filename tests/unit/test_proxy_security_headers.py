"""SEC.5 (item 5): security headers on the responses the reverse proxy sends itself.

Django never serves `/media/` (no media view on main): Caddy serves the public image files from
the volume (deploy/caddy/Caddyfile, SEC.4) and every other response comes from the app, whose
headers `tests/integration/test_security_headers_by_response_type.py` checks. These tests read
the Caddyfile, as `test_quadlets.py` does; the xfails are findings for the deploy owner.
"""

import re
from pathlib import Path

import pytest

CADDYFILE = Path(__file__).resolve().parents[2] / "deploy" / "caddy" / "Caddyfile"


def site_block() -> str:
    text = CADDYFILE.read_text()
    block = re.search(r"^\{\$MPMV_SITE_ADDRESS\} \{\n(.*?)\n\}$", text, re.M | re.S)
    assert block, "the site block must exist"
    return block.group(1)


def public_media_block() -> str:
    block = re.search(r"^\thandle @public_media \{\n(.*?)\n\t\}$", site_block(), re.M | re.S)
    assert block, "allowed media must be served by Caddy from the volume"
    return block.group(1)


def test_media_is_not_sniffed_and_runs_no_scripts() -> None:
    body = public_media_block()
    assert re.search(r"^\t\theader X-Content-Type-Options nosniff$", body, re.M)
    csp = re.findall(r"^\t\theader(?: @svg)? Content-Security-Policy \"([^\"]*)\"$", body, re.M)
    assert csp
    for policy in csp:
        assert "default-src 'none'" in policy
        assert "sandbox" in policy
        assert "script-src" not in policy


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Finding: Caddy sends `Server: Caddy` on every response; the site block has no "
        "`header -Server`."
    ),
)
def test_proxy_drops_the_server_header() -> None:
    assert re.search(r"^\theader -Server$", site_block(), re.M)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Finding: public media served by Caddy carry no Strict-Transport-Security, no "
        "Referrer-Policy and no frame-ancestors (sandbox does not stop framing); the app's "
        "headers never reach them."
    ),
)
def test_media_carries_the_transport_and_framing_headers() -> None:
    body = public_media_block()
    assert re.search(r"^\t\theader Strict-Transport-Security ", body, re.M)
    assert re.search(r"^\t\theader Referrer-Policy ", body, re.M)
    csp = re.findall(r"^\t\theader(?: @svg)? Content-Security-Policy \"([^\"]*)\"$", body, re.M)
    assert csp
    assert all("frame-ancestors 'none'" in policy for policy in csp)
