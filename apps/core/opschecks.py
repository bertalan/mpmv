"""Pure alert rules for database disk space, WAL archiving lag and backup age (OPS.2).

No ORM and no I/O: `apps.core.alerts` reads the numbers and calls these.
"""

import dataclasses
import datetime as dt


@dataclasses.dataclass(frozen=True)
class Alert:
    key: str
    summary: str
    details: dict[str, str] = dataclasses.field(default_factory=dict)


def disk_alert(
    label: str, *, free_bytes: int, total_bytes: int, min_free_percent: float
) -> Alert | None:
    free_percent = 0.0 if total_bytes <= 0 else free_bytes * 100 / total_bytes
    if free_percent >= min_free_percent:
        return None
    return Alert(
        f"disk:{label}",
        f"Free disk space on {label} is below {min_free_percent:g}%",
        {"free_percent": f"{free_percent:.1f}", "min_free_percent": f"{min_free_percent:g}"},
    )


def wal_archive_alert(
    last_archived_at: dt.datetime | None,
    last_failed_at: dt.datetime | None,
    now: dt.datetime,
    *,
    max_lag_seconds: int,
) -> Alert | None:
    if last_archived_at is None:
        return Alert("wal:never", "No WAL segment was ever archived")
    if last_failed_at is not None and last_failed_at > last_archived_at:
        return Alert(
            "wal:failing",
            "WAL archiving failed after the last success",
            {"last_failed_at": last_failed_at.isoformat()},
        )
    lag = (now - last_archived_at).total_seconds()
    if lag > max_lag_seconds:
        return Alert(
            "wal:lag",
            "The last WAL archive is older than the allowed lag",
            {"lag_seconds": str(int(lag)), "max_lag_seconds": str(max_lag_seconds)},
        )
    return None


def backup_age_alert(
    last_backup_at: dt.datetime | None, now: dt.datetime, *, max_age_hours: int
) -> Alert | None:
    if last_backup_at is None:
        return Alert("backup:missing", "No backup is recorded")
    age_hours = (now - last_backup_at).total_seconds() / 3600
    if age_hours > max_age_hours:
        return Alert(
            "backup:age",
            "The last backup is older than the allowed age",
            {"age_hours": f"{age_hours:.1f}", "max_age_hours": str(max_age_hours)},
        )
    return None
