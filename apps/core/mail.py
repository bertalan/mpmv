"""Transactional email building blocks without Django (ARC.16, spec 01 §1.2).

- `EmailSender` is the interface every provider sits behind. `MemoryEmailSender` (tests) and
  `ConsoleEmailSender` (development) ship here; the EU provider adapter comes with ARC.19, once
  the owner picks the provider (Q-48).
- The retry schedule (`retry_delay`) is the one the task queue applies to `core.send_email`,
  so the outbox's `next_attempt_at` is when the queue runs the job again.
- `html_violations` enforces the HTML rules: no tracking pixel and no remote resource of any
  kind (Garante provision 284/2026, spec 11), and nothing the CSP would block (no scripts,
  inline styles or event handlers, spec 12 SEC.1).
- `SignedJsonFeedback` is the provider-neutral feedback format of the webhook stub: HMAC-signed
  JSON with hard bounces, soft bounces and complaints. A provider adapter offers the same
  `FeedbackParser` interface for its own format.

The outbox, the rendering and the suppression list live in `apps.core.services`.
"""

import hashlib
import hmac
import itertools
import json
import sys
from collections import deque
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Final, Protocol, TextIO, runtime_checkable

from apps.core.text import ascii_int

# --- Messages and senders -------------------------------------------------------------------


@dataclass(frozen=True)
class RenderedEmail:
    subject: str
    text_body: str
    html_body: str


@dataclass(frozen=True)
class Attachment:
    """A file sent with an email (ORD.9 3/3: the non-fiscal confirmation PDF)."""

    filename: str
    content_type: str
    content: bytes = field(repr=False)


@dataclass(frozen=True)
class OutgoingEmail:
    recipient: str
    from_address: str
    subject: str
    text_body: str
    html_body: str
    #: Stable per outbox row: providers that accept an idempotency key drop a repeated send.
    idempotency_key: str
    attachments: tuple[Attachment, ...] = ()


@dataclass(frozen=True)
class SendResult:
    provider_message_id: str


class SendError(Exception):
    """The provider did not accept the email."""


class TransientSendError(SendError):
    """Worth retrying later: timeout, rate limit, provider outage, 4xx greylisting."""


class PermanentSendError(SendError):
    """Never worth retrying: invalid address, rejected content, account refused."""


@runtime_checkable
class EmailSender(Protocol):
    def send(self, email: OutgoingEmail) -> SendResult:
        """Hand `email` to the provider; raise TransientSendError or PermanentSendError."""
        ...


@dataclass
class MemoryEmailSender:
    """Test backend: keeps what it sends; `fail_next` scripts provider failures."""

    sent: list[OutgoingEmail] = field(default_factory=list)
    attempts: int = 0
    _failures: deque[SendError] = field(default_factory=deque)
    _ids: Iterator[int] = field(default_factory=lambda: itertools.count(1))

    def fail_next(self, *errors: SendError) -> None:
        """The next sends raise these errors, in order, before sending again."""
        self._failures.extend(errors)

    def clear(self) -> None:
        self.sent.clear()
        self._failures.clear()
        self.attempts = 0

    def send(self, email: OutgoingEmail) -> SendResult:
        self.attempts += 1
        if self._failures:
            raise self._failures.popleft()
        self.sent.append(email)
        return SendResult(provider_message_id=f"memory-{next(self._ids)}")


class ConsoleEmailSender:
    """Development backend: prints the email (text part) instead of sending it."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self.stream = stream

    def send(self, email: OutgoingEmail) -> SendResult:
        out = self.stream or sys.stdout
        files = "".join(
            f"Attachment: {a.filename} ({a.content_type}, {len(a.content)} bytes)\n"
            for a in email.attachments
        )
        out.write(
            f"From: {email.from_address}\nTo: {email.recipient}\nSubject: {email.subject}\n"
            f"Idempotency-Key: {email.idempotency_key}\n{files}\n{email.text_body}\n{'-' * 72}\n"
        )
        out.flush()
        return SendResult(provider_message_id=f"console-{email.idempotency_key}")


def normalise_address(address: str) -> str:
    """The form addresses are compared in (suppression list)."""
    return address.strip().lower()


# --- Retries --------------------------------------------------------------------------------

MAX_RETRIES: Final = 5  # retries after the first attempt
BASE_DELAY: Final = 60  # seconds before the first retry; doubles each time
MAX_DELAY: Final = 3600


def retry_delay(attempts: int) -> int | None:
    """Seconds before the next attempt after `attempts` sends; None once retries are spent."""
    if attempts > MAX_RETRIES:
        return None
    return int(min(BASE_DELAY * 2 ** (attempts - 1), MAX_DELAY))


# --- HTML rules -----------------------------------------------------------------------------

# Elements that load something or run code. Images are refused outright: any image an email
# loads from a server can track its opening, and inline `cid:` images are not needed yet.
_FORBIDDEN_TAGS: Final = frozenset(
    {
        "img",
        "picture",
        "source",
        "video",
        "audio",
        "track",
        "iframe",
        "frame",
        "object",
        "embed",
        "applet",
        "script",
        "style",
        "link",
        "base",
        "form",
        "svg",
        "math",
    }
)
_FORBIDDEN_ATTRIBUTES: Final = frozenset(
    {"style", "src", "srcset", "background", "poster", "data", "lowsrc", "dynsrc", "ping"}
)
_UNSAFE_SCHEMES: Final = ("javascript:", "vbscript:", "data:")


class _Linter(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.violations: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _FORBIDDEN_TAGS:
            self.violations.append(f"<{tag}> is not allowed")
        if tag == "meta" and any(n == "http-equiv" for n, _ in attrs):
            self.violations.append("<meta http-equiv> is not allowed")
        for name, value in attrs:
            if name.startswith("on"):
                self.violations.append(f"event handler {name}= on <{tag}>")
            elif name in _FORBIDDEN_ATTRIBUTES:
                self.violations.append(f"{name}= on <{tag}> is not allowed")
            elif name == "href" and (value or "").strip().lower().startswith(_UNSAFE_SCHEMES):
                self.violations.append(f"unsafe link on <{tag}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)


def html_violations(html: str) -> list[str]:
    """Why `html` may not be sent (empty when it is fine): no tracking pixel, no remote
    resource, no script, inline style or event handler."""
    linter = _Linter()
    linter.feed(html)
    linter.close()
    return linter.violations


# --- Provider feedback (bounces and complaints) ---------------------------------------------

HARD_BOUNCE: Final = "hard_bounce"
SOFT_BOUNCE: Final = "soft_bounce"
COMPLAINT: Final = "complaint"
FEEDBACK_KINDS: Final = frozenset({HARD_BOUNCE, SOFT_BOUNCE, COMPLAINT})
#: Feedback after which the address receives no more email.
SUPPRESSING_KINDS: Final = frozenset({HARD_BOUNCE, COMPLAINT})


@dataclass(frozen=True)
class FeedbackEvent:
    kind: str  # one of FEEDBACK_KINDS
    recipient: str  # normalised
    provider_message_id: str = ""  # empty when the provider does not say
    detail: str = ""  # e.g. the SMTP status, for staff


class InvalidFeedback(ValueError):
    """The webhook body is not a feedback payload."""


class FeedbackParser(Protocol):
    def verify(self, headers: Mapping[str, str], body: bytes, *, now: float) -> bool:
        """Is the request signed by the provider, and recent?"""
        ...

    def parse(self, body: bytes) -> list[FeedbackEvent]:
        """The events of a verified body; raise InvalidFeedback for anything else."""
        ...


SIGNATURE_HEADER: Final = "X-Mpmv-Signature"
TIMESTAMP_HEADER: Final = "X-Mpmv-Timestamp"


class SignedJsonFeedback:
    """The webhook stub's format: `{"events": [{"type", "recipient", "message_id", "detail"}]}`
    signed with HMAC-SHA256 over `<timestamp>.<body>`, refused when older than `tolerance`."""

    def __init__(self, secret: str, *, tolerance: int = 300) -> None:
        if not secret:
            raise ValueError("a webhook secret is required")
        self._secret = secret.encode()
        self.tolerance = tolerance

    def _signature(self, body: bytes, timestamp: str) -> str:
        return hmac.new(self._secret, timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()

    def sign(self, body: bytes, *, timestamp: int) -> dict[str, str]:
        """The headers a sender attaches (tests, and a relay in front of a provider)."""
        stamp = str(timestamp)
        return {TIMESTAMP_HEADER: stamp, SIGNATURE_HEADER: self._signature(body, stamp)}

    def verify(self, headers: Mapping[str, str], body: bytes, *, now: float) -> bool:
        stamp = headers.get(TIMESTAMP_HEADER, "")
        signature = headers.get(SIGNATURE_HEADER, "")
        when = ascii_int(stamp)
        if when is None or not signature:
            return False
        if abs(now - when) > self.tolerance:
            return False
        return hmac.compare_digest(signature, self._signature(body, stamp))

    def parse(self, body: bytes) -> list[FeedbackEvent]:
        try:
            payload = json.loads(body)
        except (ValueError, RecursionError) as exc:
            raise InvalidFeedback("the body is not JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
            raise InvalidFeedback('expected an object with an "events" list')
        return [_event(item) for item in payload["events"]]


def _event(item: object) -> FeedbackEvent:
    if not isinstance(item, dict):
        raise InvalidFeedback("an event must be an object")
    kind, recipient = item.get("type"), item.get("recipient")
    if not isinstance(kind, str) or kind not in FEEDBACK_KINDS:
        raise InvalidFeedback(f"unknown event type {kind!r}")
    if not isinstance(recipient, str) or "@" not in recipient:
        raise InvalidFeedback("an event needs a recipient address")
    return FeedbackEvent(
        kind=kind,
        recipient=normalise_address(recipient),
        provider_message_id=str(item.get("message_id") or ""),
        detail=str(item.get("detail") or "")[:500],
    )
