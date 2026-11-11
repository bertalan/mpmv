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
import unicodedata
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


# Every pattern below runs in the request thread, so each is linear in the input: matches start
# only at the start of a run (lookbehind), repeats are possessive or bounded, and the text is cut
# to MAX_TEXT before any pattern runs (tests/test_redaction_performance.py).
_KEY_STEMS: Final = (
    r"secret|passw(?:or)?d|passcode|pwd|token|api[_-]?key|apikey|private[_-]?key|session|cookie"
    r"|csrf|signature|authorization|bearer|iban|card|cvv"
)
# Short names count only as a whole key, so "shipping" and "mapping" are left alone.
_EXACT_KEYS: Final = frozenset({"pw", "pin", "pass", "otp", "key"})
_KEY_STEM_RE: Final = re.compile(rf"(?i)(?:{_KEY_STEMS})")
_EMAIL: Final = re.compile(r"(?<![\w.+-])[\w.+-]++@[\w-]++(?:\.[\w-]++)++")
_EU_PREFIX: Final = (
    "AT|BE|BG|CY|CZ|DE|DK|EE|EL|ES|FI|FR|HR|HU|IE|IT|LT|LU|LV|MT|NL|PL|PT|RO|SE|SI|SK|XI"
)
_NOT_ALNUM_BEFORE: Final = r"(?<![A-Za-z0-9])"
_NOT_ALNUM_AFTER: Final = r"(?![A-Za-z0-9])"
_QUOTE: Final = r"(?:\\?[\"'])?"
# A sensitive key: a word (a run of word characters and dashes, matched once from its start)
# that holds a stem, or one of the exact short names.
_KEY: Final = rf"(?:(?=[\w-]*?(?:{_KEY_STEMS}))[\w-]++|(?:pw|pin|pass|otp|key)(?![\w-]))"
_VALUE: Final = (
    r"(?!\[redacted\])(?:\\\"(?:[^\"\\]++|\\(?!\"))*+\\\""  # a string escaped inside a string
    r"|\"[^\"]*+\"|'[^']*+'|[^\s,;&\"'})\]]++)"
)
_PATTERNS: Final = (
    # URL credentials: scheme://user:pass@host, scheme://token@host
    (re.compile(r"(?<=://)[^/@\s]++@"), f"{REDACTED}@"),
    # Authorization header of any scheme: everything to the end of the line or the quote
    (
        re.compile(r"(?i)(authorization[\"']?\s*+[=:]\s*+[\"']?)[^\r\n\"']++"),
        rf"\1{REDACTED}",
    ),
    (re.compile(r"(?i)\b(?:Bearer|Basic|Digest|Token)\s++[A-Za-z0-9._~+/=-]{8,}+"), REDACTED),
    # key=value, key: value, "key": "value", 'key': 'value' for sensitive keys
    (
        re.compile(rf"(?i)({_QUOTE}(?<![\w-]){_KEY}{_QUOTE}\s*+[=:]\s*+){_VALUE}"),
        rf"\1{REDACTED}",
    ),
    # bare JWT: three base64url segments
    (re.compile(r"(?<![\w-])eyJ[\w-]{5,}+\.[\w-]{5,}+\.[\w-]*+"), REDACTED),
    # provider key shapes and long high-entropy tokens after `=`
    (re.compile(r"(?i)(?<![\w-])(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]++"), REDACTED),
    (re.compile(r"(?<![\w-])(?:sk|pk|rk)_[A-Za-z0-9]{20,}+"), REDACTED),
    (re.compile(r"(?<![\w-])whsec_[A-Za-z0-9]++"), REDACTED),
    (re.compile(r"(=\s*+)[A-Za-z0-9_+/-]{32,}+"), rf"\1{REDACTED}"),
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
            r"(?i)(?<![A-Za-z0-9])(?:vat|p\.?\s?iva|ust-?idnr\.?|tva|btw|nif)(?![a-z])"
            r"(?:\s++(?:number|no\.?|id|code))?[\s:#=.-]*+(?:[A-Z]{2}[ -]?)?\d[\dA-Z .-]{6,16}"
        ),
        REDACTED,
    ),
    (
        re.compile(
            rf"(?i){_NOT_ALNUM_BEFORE}(?:{_EU_PREFIX})[ -]?(?=(?:[A-Z]{{0,11}}\d){{6}})"
            rf"[A-Z0-9]{{8,12}}{_NOT_ALNUM_AFTER}"
        ),
        REDACTED,
    ),
    (re.compile(r"(?i)(?<![\w.+-])[\w.+-]++%40[\w-]++(?:\.[\w-]++)++"), REDACTED),
    (_EMAIL, REDACTED),
    (re.compile(r"(?<![\d])(?:\d[ -]?){13,19}(?!\d)"), REDACTED),  # card-like numbers
)

# A canonical UUID is an identifier, not a secret: kept intact (its last group would otherwise
# look like an IBAN or a key). Not protected right after `=` or `:`-less key assignments, so
# `token=<uuid>` is still redacted by the generic patterns.
_UUID: Final = re.compile(
    r"(?<![\w=-])[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}(?![\w-])"
)

MAX_DEPTH: Final = 6
MAX_ITEMS: Final = 50
MAX_NODES: Final = 400
MAX_TEXT: Final = 8000
MAX_TRACE: Final = 16000
TRUNCATED: Final = " [truncated]"


def _clip(text: str, limit: int, *, keep_tail: bool = False) -> str:
    if len(text) <= limit:
        return text
    return ("..." + text[-limit:]) if keep_tail else text[:limit] + TRUNCATED


def redact_unbounded(text: str) -> str:
    """The patterns on `text` as it is (tests only; callers use `redact_text`)."""
    kept: list[str] = []

    def keep(match: re.Match[str]) -> str:
        kept.append(match.group(0))
        return f"\x00{len(kept) - 1}\x00"

    text = _UUID.sub(keep, text)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return re.sub(r"\x00(\d+)\x00", lambda m: kept[int(m.group(1))], text) if kept else text


def redact_text(text: str, *, limit: int = MAX_TEXT, keep_tail: bool = False) -> str:
    """`text` cut to `limit`, with secrets, personal data (e-mail, IBAN, VAT, card numbers) and
    tokens removed.

    The cut comes first, so the work is bounded. Non-ASCII text is NFKC-normalised first, so
    fullwidth letters and digits cannot hide a value. It prefers over-redaction: an order number
    of 13 or more digits looks like a card number.
    """
    text = _clip(text, limit, keep_tail=keep_tail)
    if not text.isascii():
        text = _clip(unicodedata.normalize("NFKC", text), limit, keep_tail=keep_tail)
    return redact_unbounded(redact_secret_paths(text))


def is_sensitive_key(key: object) -> bool:
    name = str(key)[:200]
    return name.lower() in _EXACT_KEYS or _KEY_STEM_RE.search(name) is not None


def redact_value(value: Any, *, depth: int = 0, budget: list[int] | None = None) -> Any:
    """A redacted copy of `value`: dicts, lists, tuples and sets recursively (by key name and by
    value pattern), other objects through their text. Depth, items per container, total nodes
    and text length are limited; the caller's object is never changed."""
    if budget is None:
        budget = [MAX_NODES]
    budget[0] -= 1
    if value is None or isinstance(value, bool):
        return value
    if depth > MAX_DEPTH or budget[0] < 0:
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
            out[redact_text(str(key), limit=200)] = (
                REDACTED
                if is_sensitive_key(key)
                else redact_value(item, depth=depth + 1, budget=budget)
            )
        return out
    if isinstance(value, list | tuple | set | frozenset):
        items = [
            redact_value(item, depth=depth + 1, budget=budget) for item in list(value)[:MAX_ITEMS]
        ]
        if len(value) > MAX_ITEMS:
            items.append(f"... {len(value) - MAX_ITEMS} more")
        return items
    try:
        text = str(value)
    except Exception:  # an object's own __str__ must not break logging
        return "[unrepresentable]"
    return redact_text(text)


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
            record.exc_text = redact_text(record.exc_text, limit=MAX_TRACE, keep_tail=True)
        if record.stack_info:
            record.stack_info = redact_text(record.stack_info, limit=MAX_TRACE, keep_tail=True)
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
