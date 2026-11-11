"""Error tracking behind our own interface (OPS.2).

Code reports through `capture_exception` and `capture_message`. No external provider is chosen
yet (lane-a decision): the adapters are `NoOpTracker` (default), `LogTracker` (one ERROR log line
per event, for the JSON log pipeline) and `MemoryTracker` (tests). `settings.ERROR_TRACKER`
(env `MPMV_ERROR_TRACKER`) is `none` or `log`. A future provider is one more class implementing
`ErrorTracker`, registered in `ADAPTERS`; no DSN or SDK lives in settings or dependencies today.
The context is redacted here, and only the exception type is sent as the message: an exception
message can hold personal data.
"""

import dataclasses
import logging
from typing import Protocol, runtime_checkable

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .observability import redact_mapping

logger = logging.getLogger(__name__)


@runtime_checkable
class ErrorTracker(Protocol):
    def capture_exception(self, exc: BaseException, *, context: dict[str, str]) -> None: ...

    def capture_message(self, message: str, *, level: str, context: dict[str, str]) -> None: ...


class NoOpTracker:
    def capture_exception(self, exc: BaseException, *, context: dict[str, str]) -> None:
        return None

    def capture_message(self, message: str, *, level: str, context: dict[str, str]) -> None:
        return None


@dataclasses.dataclass(frozen=True)
class TrackedEvent:
    message: str
    level: str
    context: dict[str, str]


class MemoryTracker:
    """Keeps events in a list (tests and local development)."""

    def __init__(self) -> None:
        self.events: list[TrackedEvent] = []

    def capture_exception(self, exc: BaseException, *, context: dict[str, str]) -> None:
        self.events.append(TrackedEvent(type(exc).__name__, "error", dict(context)))

    def capture_message(self, message: str, *, level: str, context: dict[str, str]) -> None:
        self.events.append(TrackedEvent(message, level, dict(context)))


class LogTracker:
    """Writes each event as one ERROR line on `mpmv.errors` (context already redacted)."""

    _log = logging.getLogger("mpmv.errors")

    def capture_exception(self, exc: BaseException, *, context: dict[str, str]) -> None:
        self._log.error("exception %s %s", type(exc).__name__, context)

    def capture_message(self, message: str, *, level: str, context: dict[str, str]) -> None:
        self._log.error("event %s %s", message, context)


ADAPTERS: dict[str, type[ErrorTracker]] = {"none": NoOpTracker, "log": LogTracker}
_tracker: ErrorTracker | None = None


def get_tracker() -> ErrorTracker:
    global _tracker
    if _tracker is None:
        name = getattr(settings, "ERROR_TRACKER", "") or "none"
        if name not in ADAPTERS:
            raise ImproperlyConfigured(f"ERROR_TRACKER {name!r} must be one of {sorted(ADAPTERS)}")
        candidate = ADAPTERS[name]()
        _tracker = candidate
    return _tracker


def set_tracker(tracker: ErrorTracker) -> None:
    global _tracker
    _tracker = tracker


def reset_tracker() -> None:
    global _tracker
    _tracker = None


def capture_exception(exc: BaseException, *, context: dict[str, str] | None = None) -> None:
    """Report `exc`; a failing provider is logged and never breaks the caller."""
    try:
        get_tracker().capture_exception(exc, context=redact_mapping(context or {}))
    except ImproperlyConfigured:
        raise
    except Exception:
        logger.warning("error tracker failed to record %s", type(exc).__name__)


def capture_message(
    message: str, *, level: str = "error", context: dict[str, str] | None = None
) -> None:
    try:
        get_tracker().capture_message(message, level=level, context=redact_mapping(context or {}))
    except ImproperlyConfigured:
        raise
    except Exception:
        logger.warning("error tracker failed to record a message")


class ErrorTrackingHandler(logging.Handler):
    """Forward records with a traceback (logger.exception, 500s) to the tracker."""

    def emit(self, record: logging.LogRecord) -> None:
        if record.exc_info and isinstance(record.exc_info[1], BaseException):
            capture_exception(record.exc_info[1], context={"logger": record.name})
