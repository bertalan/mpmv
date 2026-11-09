"""SEC.5 (item 5): security headers on the responses the reverse proxy sends itself.

Caddy answers some requests without the app: public image files from the volume (SEC.4), the
404 for any other `/media/` path, its own error pages (502 when the app is down, 413 for an
oversized body) and the HTTP-to-HTTPS redirect. Every other response comes from the app, whose
headers `tests/integration/test_security_headers_by_response_type.py` checks; Django's own media
view (`apps.core.media`, used without Caddy) is covered there too.

The Caddyfile imports one `security_headers` snippet in the site block, in `handle_errors` and in
the plain-HTTP redirect block. Its headers are defaults (`?`), so the app's own values win on
proxied responses, and it deletes `Server` on everything: Caddy's own and gunicorn's (production
runs gunicorn, `deploy/container/entrypoint.sh`), which the in-app test client can never see.
These tests read the Caddyfile, as `test_quadlets.py` does.
"""

import re
from pathlib import Path

import pytest
from django.conf import settings

from apps.core.media import RASTER_CSP, SVG_CSP
from apps.core.security import build_permissions_policy
from tests.integration.test_security_headers import DENIED_FEATURES, EXPECTED_HEADERS

CADDYFILE = Path(__file__).resolve().parents[2] / "deploy" / "caddy" / "Caddyfile"

# Wagtail's per-file image cap (SEC.2, PR #164), with Django's default if it is not set yet.
IMAGE_UPLOAD_CAP = getattr(settings, "WAGTAILIMAGES_MAX_UPLOAD_SIZE", 25 * 1024 * 1024)
# A multipart form around one file adds a few KB; anything above this is not an upload we take.
FORM_OVERHEAD_LIMIT = 4 * 1024 * 1024


def top_level_block(head: str) -> str:
    text = CADDYFILE.read_text()
    block = re.search(rf"^{re.escape(head)} \{{\n(.*?)\n\}}$", text, re.M | re.S)
    assert block, f"the {head} block must exist"
    return block.group(1)


def site_block() -> str:
    return top_level_block("{$MPMV_SITE_ADDRESS}")


def public_media_block() -> str:
    block = re.search(r"^\thandle @public_media \{\n(.*?)\n\t\}$", site_block(), re.M | re.S)
    assert block, "allowed media must be served by Caddy from the volume"
    return block.group(1)


def snippet_headers() -> dict[str, str]:
    """{field: operation} of the `security_headers` snippet's single `header` block."""
    snippet = top_level_block("(security_headers)")
    block = re.search(r"^\theader \{\n(.*?)\n\t\}$", snippet, re.M | re.S)
    assert block, "the snippet must be one header block"
    fields: dict[str, str] = {}
    for line in block.group(1).splitlines():
        field, _, value = line.strip().partition(" ")
        fields[field] = value.strip('"')
    return fields


def test_media_is_not_sniffed_and_runs_no_scripts() -> None:
    body = public_media_block()
    assert re.search(r"^\t\theader X-Content-Type-Options nosniff$", body, re.M)
    csp = re.findall(r"^\t\theader(?: @\w+)? Content-Security-Policy \"([^\"]*)\"$", body, re.M)
    assert csp
    for policy in csp:
        assert "default-src 'none'" in policy
        assert "sandbox" in policy
        assert "script-src" not in policy


def test_the_snippet_drops_the_server_header() -> None:
    """`Server: Caddy` and gunicorn's `Server: gunicorn` (passed through reverse_proxy)."""
    fields = snippet_headers()
    assert fields["-Server"] == ""
    assert fields["-Via"] == "", "reverse_proxy adds `Via: 1.0 Caddy`"


def test_the_snippet_sets_the_app_header_set_as_defaults_only() -> None:
    """Same values as the app (SEC.3), with `?` so a header the app already set is kept."""
    fields = snippet_headers()
    for name, value in EXPECTED_HEADERS.items():
        assert fields[f"?{name}"] == value, name
    assert not [field for field in fields if field[0] not in "?-"], "never overwrite the app"
    csp = fields["?Content-Security-Policy"]
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert fields["?Permissions-Policy"] == build_permissions_policy(settings.PERMISSIONS_POLICY)
    permissions = {item.strip() for item in fields["?Permissions-Policy"].split(",")}
    assert {f"{feature}=()" for feature in DENIED_FEATURES} <= permissions


@pytest.mark.parametrize(
    "head", ["{$MPMV_SITE_ADDRESS}", "http://{$MPMV_SITE_ADDRESS}"], ids=["https", "redirect"]
)
def test_every_site_imports_the_snippet_first(head: str) -> None:
    """Covers the media files, the `/media/` 404 and the redirect, which the app never sees."""
    assert top_level_block(head).splitlines()[0] == "\timport security_headers"


def test_caddy_error_pages_carry_the_snippet() -> None:
    """502 (app down), 413 (body too large) and any other error Caddy answers itself."""
    errors = re.search(r"^\thandle_errors \{\n(.*?)\n\t\}$", site_block(), re.M | re.S)
    assert errors, "Caddy's own errors need the headers too"
    lines = errors.group(1).splitlines()
    assert lines[0] == "\t\timport security_headers"
    assert '\t\trespond "{err.status_code}" {err.status_code}' in lines, (
        "status only, no error text"
    )


def test_caddy_error_pages_are_never_cached() -> None:
    """A 404 for a missing image must not inherit the media handler's public Cache-Control.

    Otherwise a rendition fetched before its file exists stays a 404 in shared caches for a day.
    """
    errors = re.search(r"^\thandle_errors \{\n(.*?)\n\t\}$", site_block(), re.M | re.S)
    assert errors
    assert '\t\theader Cache-Control "no-store"' in errors.group(1).splitlines()


def test_plain_http_redirects_to_https_for_the_site_only() -> None:
    redirect = top_level_block("http://{$MPMV_SITE_ADDRESS}")
    # The target is the configured host, never the client's Host spelling.
    assert re.search(r"^\tredir https://\{\$MPMV_SITE_ADDRESS\}\{uri\} 308$", redirect, re.M)
    assert "{host}" not in redirect


def test_media_carries_the_transport_and_framing_headers() -> None:
    """HSTS and Referrer-Policy come from the snippet; framing is denied in the media CSP."""
    fields = snippet_headers()
    assert fields["?Strict-Transport-Security"] == EXPECTED_HEADERS["Strict-Transport-Security"]
    assert fields["?Referrer-Policy"] == EXPECTED_HEADERS["Referrer-Policy"]
    assert fields["?X-Frame-Options"] == "DENY"
    csp = re.findall(
        r"^\t\theader(?: @\w+)? Content-Security-Policy \"([^\"]*)\"$", public_media_block(), re.M
    )
    assert sorted(csp) == sorted([RASTER_CSP, SVG_CSP])
    assert all("frame-ancestors 'none'" in policy for policy in csp)


def caddy_bytes(size: str) -> int:
    number, unit = re.fullmatch(r"(\d+)(MiB|KiB)", size).groups()  # type: ignore[union-attr]
    return int(number) * {"KiB": 1024, "MiB": 1024 * 1024}[unit]


def test_request_bodies_are_capped_just_above_the_upload_cap() -> None:
    """SEC.2: one image of up to the per-file cap plus its multipart form, nothing larger.

    Without a cap a multi-GB body is spooled to a temp file before Wagtail checks the size.
    """
    found = re.search(r"^\trequest_body \{\n\t\tmax_size (\S+)\n\t\}$", site_block(), re.M)
    assert found, "the site block must cap request bodies"
    cap = caddy_bytes(found.group(1))
    assert IMAGE_UPLOAD_CAP < cap <= IMAGE_UPLOAD_CAP + FORM_OVERHEAD_LIMIT
