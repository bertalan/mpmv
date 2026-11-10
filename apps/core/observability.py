"""Structured JSON logs, the request id and redaction of personal data and secrets (OPS.2).

No ORM here: it is imported by the settings' `LOGGING`, the request id middleware and the error
tracker. Logs never carry personal data or secrets: `redact_text` runs on every message and
traceback, and `RedactSensitiveData` redacts extra fields by name and by value.
"""

import contextvars
import datetime as dt
import json
import logging
import re
import traceback
from typing import Any, Final

from mpmv.log_redaction import REDACTED, redact_secret_paths

_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "mpmv_request_id", default=None
)
NO_REQUEST: Final = "-"


def current_request_id() -> str | None:
    return _request_id.get()


def set_request_id(value: str | None) -> contextvars.Token[str | None]:
    return _request_id.set(value)


def reset_request_id(token: contextvars.Token[str | None]) -> None:
    _request_id.reset(token)


_SENSITIVE_KEY: Final = (
    r"(?:pass(?:word|wd)?|secret|token|api[_-]?key|authorization|cookie|csrf\w*|session\w*"
    r"|iban|card\w*|cvv|pin)"
)
_PATTERNS: Final = (
    # `Authorization: Bearer x`, `Bearer x`
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"), f"Bearer {REDACTED}"),
    # key=value, key: value, key="value" for sensitive keys (value runs to a separator)
    (
        re.compile(rf"(?i)\b({_SENSITIVE_KEY})(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;&\"']+)"),
        rf"\1\2{REDACTED}",
    ),
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), REDACTED),  # e-mail addresses
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), REDACTED),  # card-like numbers
)
_SENSITIVE_KEY_RE: Final = re.compile(rf"(?i){_SENSITIVE_KEY}")
_EMAIL: Final = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def redact_text(text: str) -> str:
    """`text` with secrets, e-mail addresses, card-like numbers and action link tokens removed."""
    text = redact_secret_paths(text)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact_mapping(values: dict[str, Any]) -> dict[str, str]:
    """Strings for the values of `values`, sensitive keys and e-mail-bearing values redacted."""
    out: dict[str, str] = {}
    for key, value in values.items():
        text = str(value)
        out[str(key)] = (
            REDACTED
            if _SENSITIVE_KEY_RE.search(str(key)) or _EMAIL.search(text)
            else (redact_text(text))
        )
    return out


class RequestIdFilter(logging.Filter):
    """Stamp `record.request_id` (the id of the request being served, `-` outside one)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current_request_id() or NO_REQUEST
        return True


class RedactSensitiveData(logging.Filter):
    """Redact the message, the traceback text and the extra fields of a record; never drops one."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = redact_text(message)
        if redacted != message:
            record.msg, record.args = redacted, None
        if record.exc_info and not record.exc_text:
            record.exc_text = "".join(traceback.format_exception(*record.exc_info))
        if record.exc_text:
            record.exc_text = redact_text(record.exc_text)
        for key in _extra_keys(record):
            value = getattr(record, key)
            if _SENSITIVE_KEY_RE.search(key) or _EMAIL.search(str(value)):
                setattr(record, key, REDACTED)
            elif isinstance(value, str):
                setattr(record, key, redact_text(value))
        return True


_STANDARD: Final = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
    "request_id",
    "taskName",
}


def _extra_keys(record: logging.LogRecord) -> list[str]:
    return [key for key in record.__dict__ if key not in _STANDARD]


class JsonFormatter(logging.Formatter):
    """One JSON object per line: time, level, logger, message, request id, exception, extras."""

    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "time": dt.datetime.fromtimestamp(record.created, tz=dt.UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", current_request_id() or NO_REQUEST),
        }
        if record.exc_info and record.exc_info[0] is not None:
            data["exc_type"] = record.exc_info[0].__name__
            data["exc_text"] = record.exc_text or "".join(
                traceback.format_exception(*record.exc_info)
            )
        for key in _extra_keys(record):
            data[key] = getattr(record, key)
        return json.dumps(data, default=str, ensure_ascii=False)
