"""Alert hooks (OPS.2): staff alerts for checkout errors and for the host's disk, WAL and backup.

An alert is an ERROR log line (the log pipeline alerts on it), an error tracker event, and an
e-mail to staff through the transactional outbox. The e-mail is sent once per alert key and
repeat window (`OPS_ALERT_REPEAT_HOURS`), so a periodic check does not flood the inbox; the
log line and the tracker event happen on every occurrence. An alert never carries personal data:
only the exception type and redacted context.

The host calls `manage.py check_ops` (cron or a systemd timer). Thresholds are settings.
"""

import datetime as dt
import hashlib
import logging
import re
import shutil
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.utils import timezone

from . import errortracking
from .observability import redact_mapping
from .opschecks import Alert, backup_age_alert, disk_alert, wal_archive_alert
from .services import NoPlatformOperator, current_operator, enqueue_email

logger = logging.getLogger("mpmv.alerts")
EMAIL_TEMPLATE = "core.ops_alert"


def alert_address() -> str:
    """`OPS_ALERT_EMAIL`, else the platform operator's support address ("" when neither)."""
    configured = str(getattr(settings, "OPS_ALERT_EMAIL", "")).strip()
    if configured:
        return configured
    try:
        return current_operator().support_email.strip()
    except NoPlatformOperator:
        return ""


def raise_alert(
    alert: Alert,
    *,
    now: dt.datetime | None = None,
    exc: BaseException | None = None,
    dedup: str = "",
) -> None:
    """Log, track (as `exc` when given) and (once per repeat window) e-mail `alert`."""
    moment = now or timezone.now()
    details = redact_mapping(alert.details)
    logger.error("ALERT %s: %s %s", alert.key, alert.summary, details)
    context = {"alert": alert.key, **details}
    if exc is not None:
        errortracking.capture_exception(exc, context=context)
    else:
        errortracking.capture_message(
            f"{alert.key}: {alert.summary}", level="error", context=context
        )
    recipient = alert_address()
    if not recipient:
        logger.warning("alert %s raised and no alert e-mail address is configured", alert.key)
        return
    window = int(getattr(settings, "OPS_ALERT_REPEAT_HOURS", 6)) * 3600
    bucket = int(moment.timestamp()) // window
    enqueue_email(
        recipient=recipient,
        template_key=EMAIL_TEMPLATE,
        language=settings.LANGUAGE_CODE,
        context={"key": alert.key, "summary": alert.summary, "details": details},
        idempotency_key=f"ops:alert:{alert.key}{dedup}:{bucket}",
    )


def message_fingerprint(exc: BaseException) -> str:
    """`:` plus a short stable hash of the message with digit runs ignored: a different failure
    of one class is mailed separately, an id in the text does not defeat de-duplication."""
    stable = re.sub(r"\d+", "#", str(exc))
    return ":" + hashlib.sha256(stable.encode()).hexdigest()[:8]


def report_checkout_error(exc: BaseException, *, context: dict[str, str] | None = None) -> None:
    """Call from the checkout's error path: track the exception and alert staff.

    Only the exception type is reported; its message may hold customer data. Call it after the
    checkout's transaction has rolled back (from the `except` clause, outside `atomic`): the
    e-mail goes through the outbox in the caller's transaction, so inside a block that rolls
    back it would be lost. The log line and the tracker event are not affected. (`on_commit`
    would not help: its callbacks are discarded on a rollback too.)
    """
    safe = redact_mapping(context or {})
    raise_alert(
        Alert(
            f"checkout:{type(exc).__name__}",
            "A checkout failed with an unexpected error",
            {"area": "checkout", "exception": type(exc).__name__, **safe},
        ),
        exc=exc,
        dedup=message_fingerprint(exc),
    )


def _read_wal_archiver() -> tuple[dt.datetime | None, dt.datetime | None]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT last_archived_time, last_failed_time FROM pg_stat_archiver")
        row = cursor.fetchone()
    return (row[0], row[1]) if row else (None, None)


def _read_backup_time(path: Path) -> tuple[dt.datetime | None, bool]:
    """(time of the last backup, readable): the status file holds one ISO 8601 timestamp."""
    try:
        text = path.read_text().strip()
    except FileNotFoundError:
        return None, True
    except OSError:
        return None, False
    try:
        value = dt.datetime.fromisoformat(text)
    except ValueError:
        return None, False
    return (value if value.tzinfo else value.replace(tzinfo=dt.UTC)), True


def collect_alerts(now: dt.datetime | None = None) -> list[Alert]:
    """Evaluate the configured checks; nothing configured, nothing checked."""
    moment = now or timezone.now()
    alerts: list[Alert | None] = []
    min_free = float(getattr(settings, "OPS_DISK_MIN_FREE_PERCENT", 15))
    for raw in getattr(settings, "OPS_DISK_PATHS", []):
        try:
            usage = shutil.disk_usage(raw)
        except OSError:
            alerts.append(Alert(f"disk:{raw}", f"Disk usage of {raw} cannot be read"))
            continue
        alerts.append(
            disk_alert(
                raw, free_bytes=usage.free, total_bytes=usage.total, min_free_percent=min_free
            )
        )
    if getattr(settings, "OPS_WAL_ARCHIVE_CHECK", False):
        archived, failed = _read_wal_archiver()
        alerts.append(
            wal_archive_alert(
                archived,
                failed,
                moment,
                max_lag_seconds=int(getattr(settings, "OPS_WAL_MAX_LAG_SECONDS", 900)),
            )
        )
    status_file = str(getattr(settings, "OPS_BACKUP_STATUS_FILE", ""))
    if status_file:
        last, readable = _read_backup_time(Path(status_file))
        if not readable:
            alerts.append(Alert("backup:unreadable", "The backup status file cannot be read"))
        else:
            alerts.append(
                backup_age_alert(
                    last,
                    moment,
                    max_age_hours=int(getattr(settings, "OPS_BACKUP_MAX_AGE_HOURS", 26)),
                )
            )
    return [alert for alert in alerts if alert is not None]


def run_ops_checks(now: dt.datetime | None = None) -> list[Alert]:
    """Collect and raise the alerts; returns them."""
    moment = now or timezone.now()
    alerts = collect_alerts(moment)
    for alert in alerts:
        raise_alert(alert, now=moment)
    return alerts
