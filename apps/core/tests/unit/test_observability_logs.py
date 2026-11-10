"""JSON logs, request id and redaction (OPS.2)."""

import json
import logging
import re
import secrets

import pytest
from django.http import HttpRequest, HttpResponse
from django.http.response import HttpResponseBase
from django.test import RequestFactory

from apps.core.middleware import RequestIdMiddleware
from apps.core.observability import (
    JsonFormatter,
    RedactSensitiveData,
    RequestIdFilter,
    current_request_id,
    redact_text,
)


def _record(message: str, *args: object, level: int = logging.INFO) -> logging.LogRecord:
    return logging.LogRecord("mpmv.test", level, __file__, 1, message, args or None, None)


def _emit(record: logging.LogRecord) -> dict[str, object]:
    for flt in (RequestIdFilter(), RedactSensitiveData()):
        flt.filter(record)
    return json.loads(JsonFormatter().format(record))  # type: ignore[no-any-return]


def test_a_record_is_one_json_line_with_the_standard_fields() -> None:
    out = _emit(_record("order %s placed", "A-1", level=logging.WARNING))
    assert out["message"] == "order A-1 placed"
    assert out["level"] == "WARNING"
    assert out["logger"] == "mpmv.test"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT.*(Z|\+00:00)", str(out["time"]))


def test_an_exception_is_logged_as_type_and_redacted_traceback() -> None:
    try:
        raise ValueError("bad value for ada@example.org")
    except ValueError:
        import sys

        record = logging.LogRecord("x", logging.ERROR, __file__, 1, "boom", None, sys.exc_info())
    out = _emit(record)
    assert out["exc_type"] == "ValueError"
    assert "ada@example.org" not in json.dumps(out)
    assert "Traceback" in str(out["exc_text"])


_API_KEY = secrets.token_hex(8)  # random per run; the parametrize id is fixed


@pytest.mark.parametrize(
    "text",
    [
        "contact ada@example.org now",
        "Authorization: Bearer abc.def-123_xyz",
        "password=hunter2hunter2",
        pytest.param(f"api_key: {_API_KEY}", id="api-key"),
        'token="sekret-value"',
        "card 4111 1111 1111 1111 declined",
        "csrftoken=abcdef; sessionid=ffee00",
    ],
)
def test_personal_data_and_secrets_never_reach_the_output(text: str) -> None:
    out = json.dumps(_emit(_record(text)))
    for leaked in (
        "ada@example.org",
        "abc.def-123_xyz",
        "hunter2hunter2",
        _API_KEY,
        "sekret-value",
        "4111 1111",
        "abcdef;",
        "ffee00",
    ):
        assert leaked not in out
    assert "[redacted]" in out


def test_harmless_text_is_left_alone() -> None:
    assert redact_text("order 1234 shipped in 3 days") == "order 1234 shipped in 3 days"


def test_extra_fields_are_included_and_sensitive_keys_are_redacted() -> None:
    record = _record("hello")
    record.order_ref = "A-1"
    record.password = "x" * 12
    record.contact = "ada@example.org"
    out = _emit(record)
    assert out["order_ref"] == "A-1"
    assert out["password"] == "[redacted]"
    assert out["contact"] == "[redacted]"


def test_the_action_link_path_stays_redacted_in_the_json_message() -> None:
    out = _emit(_record("GET /account/link/s3cr3t-token/ 404"))
    assert "s3cr3t-token" not in json.dumps(out)


def _run(request: HttpRequest) -> tuple[HttpResponseBase, str | None]:
    seen: list[str | None] = []

    def view(_: HttpRequest) -> HttpResponse:
        seen.append(current_request_id())
        return HttpResponse("ok")

    response = RequestIdMiddleware(view)(request)
    return response, seen[0]


def test_every_response_carries_a_request_id_that_the_logs_see() -> None:
    response, seen = _run(RequestFactory().get("/"))
    assert seen is not None
    assert response["X-Request-ID"] == seen
    assert current_request_id() is None  # cleared after the request


def test_a_sane_inbound_request_id_is_kept() -> None:
    response, seen = _run(RequestFactory().get("/", headers={"x-request-id": "abc-123-DEF-456"}))
    assert seen == response["X-Request-ID"] == "abc-123-DEF-456"


@pytest.mark.parametrize("bad", ["x", "a b c d e f g h", "id\nInjected: 1", "é" * 12, "a" * 200])
def test_a_hostile_inbound_request_id_is_replaced(bad: str) -> None:
    request = RequestFactory().get("/")
    request.META["HTTP_X_REQUEST_ID"] = bad
    response, seen = _run(request)
    assert seen == response["X-Request-ID"] != bad
    assert re.fullmatch(r"[0-9a-f]{32}", str(seen))


def test_the_filter_stamps_the_request_id_on_records() -> None:
    record = _record("hi")
    RequestIdFilter().filter(record)
    assert record.__dict__["request_id"] == "-"
