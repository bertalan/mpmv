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
from collections.abc import Mapping
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


_KEY_STEMS: Final = (
    r"secret|passw(?:or)?d|pwd|token|api[_-]?key|apikey|private[_-]?key|session|cookie|csrf"
    r"|signature|authorization|iban|card|cvv"
)
# Short names count only as a whole key, so "shipping" and "mapping" are left alone.
_KEY: Final = rf"(?:[\w-]*(?:{_KEY_STEMS})[\w-]*|pw|pin|pass)"
_SENSITIVE_KEY_RE: Final = re.compile(rf"(?i)(?:.*(?:{_KEY_STEMS}).*|pw|pin|pass)")
_EMAIL: Final = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_EU_PREFIX: Final = (
    "AT|BE|BG|CY|CZ|DE|DK|EE|EL|ES|FI|FR|HR|HU|IE|IT|LT|LU|LV|MT|NL|PL|PT|RO|SE|SI|SK|XI"
)
_NOT_ALNUM_BEFORE: Final = r"(?<![A-Za-z0-9])"
_NOT_ALNUM_AFTER: Final = r"(?![A-Za-z0-9])"
_VALUE: Final = r"(?!\[redacted\])(?:\"[^\"]*\"|'[^']*'|[^\s,;&\"'})\]]+)"
_PATTERNS: Final = (
    # URL credentials: scheme://user:pass@host, scheme://token@host
    (re.compile(r"(?<=://)[^/@\s]+@"), f"{REDACTED}@"),
    # Authorization header of any scheme: everything to the end of the line or the quote
    (
        re.compile(r"(?i)(authorization[\"']?\s*[=:]\s*[\"']?)[^\r\n\"']+"),
        rf"\1{REDACTED}",
    ),
    (re.compile(r"(?i)\b(?:Bearer|Basic|Digest|Token)\s+[A-Za-z0-9._~+/=-]{8,}"), REDACTED),
    # key=value, key: value, "key": "value", 'key': 'value' for sensitive keys
    (
        re.compile(rf"(?i){_NOT_ALNUM_BEFORE}([\"']?{_KEY}[\"']?\s*[=:]\s*){_VALUE}"),
        rf"\1{REDACTED}",
    ),
    # provider key shapes and long high-entropy tokens after `=`
    (re.compile(r"(?i)\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]+"), REDACTED),
    (re.compile(r"\b(?:sk|pk|rk)_[A-Za-z0-9]{20,}"), REDACTED),
    (re.compile(r"\bwhsec_[A-Za-z0-9]+"), REDACTED),
    (re.compile(r"(=\s*)[A-Za-z0-9_+/-]{32,}"), rf"\1{REDACTED}"),
    # IBAN of any country, with or without spaces
    (
        re.compile(
            rf"(?i){_NOT_ALNUM_BEFORE}[A-Z]{{2}}\d{{2}}(?: ?[A-Z0-9]{{4}}){{2,7}}"
            rf"(?: ?[A-Z0-9]{{1,3}})?{_NOT_ALNUM_AFTER}"
        ),
        REDACTED,
    ),
    # EU VAT numbers: after a VAT word (over-redacts the words that follow), or standalone
    (
        re.compile(
            r"(?i)\b(?:vat|p\.?\s?iva|ust-?idnr\.?|tva|btw|nif)(?![a-z])"
            r"(?:\s+(?:number|no\.?|id|code))?[\s:#=.-]*(?:[A-Z]{2}[ -]?)?\d[\dA-Z .-]{6,16}"
        ),
        REDACTED,
    ),
    (
        re.compile(
            rf"(?i){_NOT_ALNUM_BEFORE}(?:{_EU_PREFIX})[ -]?(?=(?:[A-Z0-9]*\d){{6}})"
            rf"[A-Z0-9]{{8,12}}{_NOT_ALNUM_AFTER}"
        ),
        REDACTED,
    ),
    (re.compile(r"[\w.+-]+%40[\w-]+(?:\.[\w-]+)+", re.I), REDACTED),  # percent-encoded e-mail
    (_EMAIL, REDACTED),
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), REDACTED),  # card-like numbers
)

MAX_DEPTH: Final = 6
MAX_ITEMS: Final = 50
MAX_TEXT: Final = 4000


def redact_text(text: str) -> str:
    """`text` with secrets, personal data (e-mail, IBAN, VAT, card numbers) and tokens removed.

    It prefers over-redaction: an order number of 13 or more digits looks like a card number.
    """
    text = redact_secret_paths(text[:MAX_TEXT] if len(text) > MAX_TEXT * 4 else text)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def is_sensitive_key(key: object) -> bool:
    return _SENSITIVE_KEY_RE.fullmatch(str(key)) is not None


def redact_value(value: Any, *, depth: int = 0) -> Any:
    """A redacted copy of `value`: dicts, lists, tuples and sets recursively (by key name and by
    value pattern), other objects through their text. Depth and size are limited; the
    caller's object is never changed."""
    if value is None or isinstance(value, bool):
        return value
    if depth > MAX_DEPTH:
        return "[truncated]"
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, int | float):
        text = str(value)
        return REDACTED if redact_text(text) != text else value
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_ITEMS:
                out["..."] = f"{len(value) - MAX_ITEMS} more"
                break
            out[redact_text(str(key))] = (
                REDACTED if is_sensitive_key(key) else redact_value(item, depth=depth + 1)
            )
        return out
    if isinstance(value, list | tuple | set | frozenset):
        items = [redact_value(item, depth=depth + 1) for item in list(value)[:MAX_ITEMS]]
        if len(value) > MAX_ITEMS:
            items.append(f"... {len(value) - MAX_ITEMS} more")
        return items
    try:
        text = str(value)
    except Exception:  # an object's own __str__ must not break logging
        return "[unrepresentable]"
    return redact_text(text[:MAX_TEXT])


def redact_mapping(values: Mapping[str, Any]) -> dict[str, str]:
    """Strings for the values of `values`, redacted by key name and by value pattern."""
    return {
        str(key): REDACTED if is_sensitive_key(key) else str(redact_value(value))
        for key, value in values.items()
    }


class RequestIdFilter(logging.Filter):
    """Stamp `record.request_id` (the id of the request being served, `-` outside one)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current_request_id() or NO_REQUEST
        return True


class RedactSensitiveData(logging.Filter):
    """Redact the message, traceback, stack and extra fields of a record; never drops one."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = redact_text(message)
        if redacted != message:
            record.msg, record.args = redacted, None
        if record.exc_info and not record.exc_text:
            record.exc_text = "".join(traceback.format_exception(*record.exc_info))
        if record.exc_text:
            record.exc_text = redact_text(record.exc_text)
        if record.stack_info:
            record.stack_info = redact_text(record.stack_info)
        for key in _extra_keys(record):
            value = getattr(record, key)
            setattr(record, key, REDACTED if is_sensitive_key(key) else redact_value(value))
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
        if record.stack_info:
            data["stack_info"] = record.stack_info
        for key in _extra_keys(record):
            data[key] = getattr(record, key)
        return json.dumps(data, default=str, ensure_ascii=False)
