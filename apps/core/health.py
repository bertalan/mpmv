"""Readiness checks (OPS.2): the database, the cache and the media storage answer.

Cheap on purpose: one `SELECT 1`, one cache round trip, one storage `exists`. The probe answers
a status only; the reason of a failure goes to the log, never to the caller.
"""

import logging
import threading
import time

from django.core.cache import cache
from django.core.files.storage import default_storage
from django.db import connection

logger = logging.getLogger(__name__)

LOG_EVERY_SECONDS = 60
_last_logged: dict[str, float] = {}
_lock = threading.Lock()


def reset_log_throttle() -> None:
    with _lock:
        _last_logged.clear()


def _should_log(name: str) -> bool:
    """At most one ERROR line per dependency per minute: a public probe must not flood the logs."""
    now = time.monotonic()
    with _lock:
        last = _last_logged.get(name)
        if last is not None and now - last < LOG_EVERY_SECONDS:
            return False
        _last_logged[name] = now
        return True


def check_database() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()


def check_cache() -> None:
    cache.set("mpmv:readyz", "1", timeout=10)
    if cache.get("mpmv:readyz") != "1":
        raise RuntimeError("cache round trip failed")


def check_storage() -> None:
    default_storage.exists("readyz-probe")


def readiness_failures() -> list[str]:
    """Names of the checks that failed (logged, not shown)."""
    failures = []
    for name, check in (
        ("database", check_database),
        ("cache", check_cache),
        ("storage", check_storage),
    ):
        try:
            check()
        except Exception as exc:
            if _should_log(name):
                logger.error("readiness check %s failed: %s", name, type(exc).__name__)
            failures.append(name)
    return failures
