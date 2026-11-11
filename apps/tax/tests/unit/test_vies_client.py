"""TAX.5: the HTTP adapter for the EU VIES REST API behind our `ViesClient` interface.

The transport is injected, so these tests never reach VIES. The last tests run the real
urllib transport against a stub server on 127.0.0.1 (no network): VIES down and VIES too slow.
"""

import http.client
import json
import socket
import threading
import time
import urllib.error
from collections.abc import Iterator, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

import pytest
from django.conf import settings

from apps.tax import engine
from apps.tax.engine import ViesAnswer, ViesQuery
from apps.tax.tests.fakes import RefusingViesClient
from apps.tax.vies import VIES_API_URL, HttpViesClient, ViesClient, ViesHttpError, get_vies_client

DE = ViesQuery("DE", "DE", "123456789")
REQUESTER = ViesQuery("AT", "AT", "U12345678")


class RecordingTransport:
    def __init__(self, answer: Mapping[str, Any] | BaseException) -> None:
        self.answer = answer
        self.requests: list[tuple[str, dict[str, Any], float]] = []

    def __call__(self, url: str, data: Mapping[str, Any], timeout: float) -> Mapping[str, Any]:
        self.requests.append((url, dict(data), timeout))
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


VALID = {
    "countryCode": "DE",
    "vatNumber": "123456789",
    "valid": True,
    "requestIdentifier": "WAPIAAAAZ1x2y3",
    "name": "Example Customer GmbH",
    "address": "Example Strasse 1",
    "userError": "VALID",
}


def test_the_http_client_is_a_vies_client() -> None:
    assert isinstance(HttpViesClient(transport=RecordingTransport(VALID)), ViesClient)


def test_the_client_posts_the_number_and_the_requester_once_with_a_timeout() -> None:
    transport = RecordingTransport(VALID)
    client = HttpViesClient(timeout=4.0, requester=REQUESTER, transport=transport)
    answer = client.check(DE)
    assert answer.result == engine.VAT_VALID
    assert answer.request_id == "WAPIAAAAZ1x2y3"
    [(url, body, timeout)] = transport.requests  # one request: no retries in the request path
    assert url == VIES_API_URL
    assert body == {
        "countryCode": "DE",
        "vatNumber": "123456789",
        "requesterMemberStateCode": "AT",
        "requesterNumber": "U12345678",
    }
    assert timeout == 4.0


def test_without_a_requester_only_the_number_is_sent() -> None:
    transport = RecordingTransport(VALID)
    HttpViesClient(transport=transport).check(DE)
    assert transport.requests[0][1] == {"countryCode": "DE", "vatNumber": "123456789"}


def test_a_per_call_requester_replaces_the_clients_own_one() -> None:
    transport = RecordingTransport(VALID)
    vendor = ViesQuery("IT", "IT", "01234567897")
    HttpViesClient(transport=transport, requester=REQUESTER).check(DE, requester=vendor)
    body = transport.requests[0][1]
    assert (body["requesterMemberStateCode"], body["requesterNumber"]) == ("IT", "01234567897")


def test_the_greek_prefix_is_sent_as_el() -> None:
    transport = RecordingTransport({**VALID, "countryCode": "EL"})
    answer = HttpViesClient(transport=transport).check(ViesQuery("GR", "EL", "123456789"))
    assert transport.requests[0][1]["countryCode"] == "EL"
    assert answer.result == engine.VAT_VALID


@pytest.mark.parametrize(
    ("failure", "detail"),
    [
        (TimeoutError("timed out"), engine.DETAIL_TIMEOUT),
        (urllib.error.URLError(TimeoutError("timed out")), engine.DETAIL_TIMEOUT),
        (urllib.error.URLError("connection refused"), engine.DETAIL_CONNECTION),
        (ConnectionResetError("reset"), engine.DETAIL_CONNECTION),
        (ViesHttpError(503), "http_503"),
        (ValueError("not JSON"), engine.DETAIL_BAD_RESPONSE),
        (http.client.IncompleteRead(b"{"), engine.DETAIL_CONNECTION),
        (http.client.BadStatusLine("garbage"), engine.DETAIL_CONNECTION),
    ],
)
def test_vies_down_or_too_slow_is_an_unavailable_answer_not_an_error(
    failure: BaseException, detail: str
) -> None:
    answer = HttpViesClient(transport=RecordingTransport(failure)).check(DE)
    assert answer == ViesAnswer(result=engine.VAT_UNAVAILABLE, detail=detail)


def test_an_error_code_in_the_body_is_kept() -> None:
    transport = RecordingTransport(
        {"actionSucceed": False, "errorWrappers": [{"error": "MS_MAX_CONCURRENT_REQ"}]}
    )
    answer = HttpViesClient(transport=transport).check(DE)
    assert answer == ViesAnswer(result=engine.VAT_UNAVAILABLE, detail="MS_MAX_CONCURRENT_REQ")


# --- The real transport against a local stub (no network)


class _StubVies(BaseHTTPRequestHandler):
    status = 200
    body: bytes = b"{}"
    delay = 0.0
    received: list[dict[str, Any]] = []  # per stub server, set by the fixture

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        type(self).received.append(json.loads(self.rfile.read(length)))
        time.sleep(self.delay)
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, format: str, *args: Any) -> None:
        pass


@pytest.fixture
def stub_vies() -> Iterator[tuple[str, type[_StubVies]]]:
    handler = type("Handler", (_StubVies,), {"received": []})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/check-vat-number", handler
    finally:
        server.shutdown()
        server.server_close()


def test_the_real_transport_reads_a_valid_answer(
    stub_vies: tuple[str, type[_StubVies]],
) -> None:
    url, handler = stub_vies
    handler.body = json.dumps(VALID).encode()
    answer = HttpViesClient(url=url, timeout=2.0).check(DE)
    assert (answer.result, answer.request_id) == (engine.VAT_VALID, "WAPIAAAAZ1x2y3")
    assert handler.received == [{"countryCode": "DE", "vatNumber": "123456789"}]


def test_the_real_transport_reads_vies_down_from_an_error_status(
    stub_vies: tuple[str, type[_StubVies]],
) -> None:
    url, handler = stub_vies
    handler.status = 500
    handler.body = json.dumps(
        {"actionSucceed": False, "errorWrappers": [{"error": "SERVICE_UNAVAILABLE"}]}
    ).encode()
    answer = HttpViesClient(url=url, timeout=2.0).check(DE)
    assert answer == ViesAnswer(result=engine.VAT_UNAVAILABLE, detail="SERVICE_UNAVAILABLE")


def test_the_real_transport_reports_an_error_status_without_a_json_body(
    stub_vies: tuple[str, type[_StubVies]],
) -> None:
    url, handler = stub_vies
    handler.status = 503
    handler.body = b"<html>maintenance</html>"
    answer = HttpViesClient(url=url, timeout=2.0).check(DE)
    assert answer == ViesAnswer(result=engine.VAT_UNAVAILABLE, detail="http_503")


def test_the_real_transport_refuses_json_that_is_not_an_object(
    stub_vies: tuple[str, type[_StubVies]],
) -> None:
    url, handler = stub_vies
    handler.body = b"[]"
    answer = HttpViesClient(url=url, timeout=2.0).check(DE)
    assert answer == ViesAnswer(result=engine.VAT_UNAVAILABLE, detail=engine.DETAIL_BAD_RESPONSE)


def test_the_real_transport_gives_up_after_the_timeout(
    stub_vies: tuple[str, type[_StubVies]],
) -> None:
    url, handler = stub_vies
    handler.delay = 1.0
    handler.body = json.dumps(VALID).encode()
    started = time.monotonic()
    answer = HttpViesClient(url=url, timeout=0.2).check(DE)
    assert answer == ViesAnswer(result=engine.VAT_UNAVAILABLE, detail=engine.DETAIL_TIMEOUT)
    assert time.monotonic() - started < 1.0


# --- The test settings never reach VIES


def test_the_default_vies_client_in_tests_refuses_every_call_loudly() -> None:
    client = get_vies_client()
    assert isinstance(client, RefusingViesClient)
    with pytest.raises(pytest.fail.Exception, match="VIES"):
        client.check(DE)
    # The services turn any Exception from a client into "VIES unavailable"; the refusal is
    # not an Exception, so it fails the test instead of passing as a fail-closed check.
    assert not issubclass(pytest.fail.Exception, Exception)


def test_the_default_vies_client_in_tests_never_opens_a_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[object] = []

    def refuse(_sock: socket.socket, address: object) -> None:
        opened.append(address)
        raise OSError("tests must not open sockets to VIES")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    with pytest.raises(pytest.fail.Exception):
        get_vies_client().check(DE)
    assert opened == []


def test_the_vies_url_in_tests_is_not_the_eu_service() -> None:
    assert settings.VIES_API_URL != VIES_API_URL
    assert urlsplit(settings.VIES_API_URL).hostname == "127.0.0.1"
