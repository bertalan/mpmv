"""Pure alert rules for disk space, WAL archiving and backup age (OPS.2)."""

import datetime as dt

from apps.core.opschecks import backup_age_alert, disk_alert, wal_archive_alert

NOW = dt.datetime(2026, 11, 10, 12, 0, tzinfo=dt.UTC)
GB = 1024**3


def test_disk_with_enough_free_space_is_quiet() -> None:
    assert disk_alert("db", free_bytes=40 * GB, total_bytes=100 * GB, min_free_percent=15) is None


def test_disk_below_the_threshold_alerts_with_numbers() -> None:
    alert = disk_alert("db", free_bytes=10 * GB, total_bytes=100 * GB, min_free_percent=15)
    assert alert is not None
    assert alert.key == "disk:db"
    assert alert.details["free_percent"] == "10.0"


def test_disk_of_zero_size_is_an_alert_not_a_division_error() -> None:
    assert disk_alert("db", free_bytes=0, total_bytes=0, min_free_percent=15) is not None


def test_wal_archiving_recent_and_clean_is_quiet() -> None:
    last = NOW - dt.timedelta(minutes=3)
    assert wal_archive_alert(last, None, NOW, max_lag_seconds=900) is None


def test_wal_archiving_late_alerts() -> None:
    last = NOW - dt.timedelta(minutes=30)
    alert = wal_archive_alert(last, None, NOW, max_lag_seconds=900)
    assert alert is not None
    assert alert.key == "wal:lag"


def test_wal_archiving_failure_after_the_last_success_alerts() -> None:
    alert = wal_archive_alert(
        NOW - dt.timedelta(minutes=2), NOW - dt.timedelta(minutes=1), NOW, max_lag_seconds=900
    )
    assert alert is not None
    assert alert.key == "wal:failing"


def test_wal_failure_long_ago_followed_by_success_is_quiet() -> None:
    assert (
        wal_archive_alert(
            NOW - dt.timedelta(minutes=1), NOW - dt.timedelta(days=1), NOW, max_lag_seconds=900
        )
        is None
    )


def test_wal_never_archived_alerts() -> None:
    alert = wal_archive_alert(None, None, NOW, max_lag_seconds=900)
    assert alert is not None
    assert alert.key == "wal:never"


def test_backup_age() -> None:
    assert backup_age_alert(NOW - dt.timedelta(hours=5), NOW, max_age_hours=26) is None
    old = backup_age_alert(NOW - dt.timedelta(hours=30), NOW, max_age_hours=26)
    assert old is not None
    assert old.key == "backup:age"
    missing = backup_age_alert(None, NOW, max_age_hours=26)
    assert missing is not None
    assert missing.key == "backup:missing"
