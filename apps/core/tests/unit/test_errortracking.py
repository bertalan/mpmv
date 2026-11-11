"""Error tracking behind our own interface (OPS.2)."""

import logging

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from apps.core import errortracking
from apps.core.errortracking import (
    ErrorTracker,
    ErrorTrackingHandler,
    LogTracker,
    MemoryTracker,
    NoOpTracker,
    get_tracker,
)


def test_the_default_tracker_is_a_no_op_that_satisfies_the_protocol() -> None:
    errortracking.reset_tracker()
    tracker = get_tracker()
    assert isinstance(tracker, NoOpTracker)
    assert isinstance(tracker, ErrorTracker)
    tracker.capture_exception(ValueError("x"), context={})  # nothing happens, nothing raised
    tracker.capture_message("m", level="error", context={})


def test_the_log_adapter_is_chosen_by_name_and_writes_one_error_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with override_settings(ERROR_TRACKER="log"):
        errortracking.reset_tracker()
        assert isinstance(get_tracker(), LogTracker)
        with caplog.at_level(logging.ERROR, logger="mpmv.errors"):
            errortracking.capture_exception(ValueError("x"), context={"a": "b"})
    errortracking.reset_tracker()
    assert [r.levelno for r in caplog.records] == [logging.ERROR]
    assert "ValueError" in caplog.records[0].getMessage()


def test_a_bad_provider_path_is_a_configuration_error() -> None:
    with override_settings(ERROR_TRACKER="sentry"):
        errortracking.reset_tracker()
        with pytest.raises(ImproperlyConfigured):
            get_tracker()
    errortracking.reset_tracker()


def test_context_is_redacted_before_it_reaches_the_provider() -> None:
    tracker = MemoryTracker()
    errortracking.set_tracker(tracker)
    try:
        errortracking.capture_exception(
            RuntimeError("boom"), context={"who": "ada@example.org", "password": "x" * 12}
        )
    finally:
        errortracking.reset_tracker()
    (event,) = tracker.events
    assert event.context == {"who": "[redacted]", "password": "[redacted]"}
    assert event.message == "RuntimeError"  # the type only: an exception message may hold data


def test_a_failing_provider_never_breaks_the_caller() -> None:
    class Broken(NoOpTracker):
        def capture_exception(self, exc: BaseException, *, context: dict[str, str]) -> None:
            raise OSError("provider down")

    errortracking.set_tracker(Broken())
    try:
        errortracking.capture_exception(ValueError("x"), context={})
    finally:
        errortracking.reset_tracker()


def test_the_log_handler_forwards_errors_with_a_traceback() -> None:
    tracker = MemoryTracker()
    errortracking.set_tracker(tracker)
    log = logging.getLogger("mpmv.test.tracker")
    handler = ErrorTrackingHandler(level=logging.ERROR)
    log.addHandler(handler)
    log.propagate = False  # the root logger has the same handler in the settings
    try:
        log.warning("ignored")
        try:
            raise ValueError("x")
        except ValueError:
            log.exception("failed")
    finally:
        log.removeHandler(handler)
        errortracking.reset_tracker()
    assert [e.message for e in tracker.events] == ["ValueError"]
