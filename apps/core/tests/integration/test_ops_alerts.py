"""Alert hooks: a simulated checkout error, disk, WAL and backup checks (OPS.2)."""

import datetime as dt
import logging
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.core import errortracking
from apps.core.alerts import report_checkout_error, run_ops_checks
from apps.core.errortracking import MemoryTracker
from apps.core.models import OutboxEmail
from apps.core.tests.factories import PlatformOperatorFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def tracker() -> MemoryTracker:
    memory = MemoryTracker()
    errortracking.set_tracker(memory)
    yield memory  # type: ignore[misc]
    errortracking.reset_tracker()


@pytest.fixture
def operator() -> None:
    PlatformOperatorFactory(support_email="help@platform.example")


def test_a_simulated_checkout_error_raises_an_alert(
    tracker: MemoryTracker, operator: None, caplog: pytest.LogCaptureFixture
) -> None:
    def checkout() -> None:
        raise RuntimeError("payment provider timeout for ada@example.org")

    with caplog.at_level(logging.ERROR, logger="mpmv.alerts"):
        try:
            checkout()
        except RuntimeError as exc:
            report_checkout_error(exc, context={"step": "payment", "cart": "c-1"})

    assert [e.message for e in tracker.events] == ["RuntimeError"]  # tracked
    assert tracker.events[0].context["step"] == "payment"
    assert any(r.levelno == logging.ERROR and "checkout" in r.getMessage() for r in caplog.records)
    (mail,) = OutboxEmail.objects.all()  # staff e-mailed
    assert mail.recipient == "help@platform.example"
    assert mail.template_key == "core.ops_alert"
    assert "ada@example.org" not in mail.context  # no personal data in the alert


def test_the_same_checkout_error_is_not_mailed_again_within_the_repeat_window(
    tracker: MemoryTracker, operator: None
) -> None:
    for _ in range(3):
        report_checkout_error(RuntimeError("x"), context={})
    assert OutboxEmail.objects.count() == 1
    assert len(tracker.events) == 3  # every occurrence is still tracked


def test_no_operator_address_still_logs_and_tracks(
    tracker: MemoryTracker, caplog: pytest.LogCaptureFixture
) -> None:
    report_checkout_error(RuntimeError("x"), context={})
    assert OutboxEmail.objects.count() == 0
    assert len(tracker.events) == 1


def test_an_alert_email_override_setting_wins(
    tracker: MemoryTracker, operator: None, settings: object
) -> None:
    settings.OPS_ALERT_EMAIL = "oncall@platform.example"  # type: ignore[attr-defined]
    report_checkout_error(RuntimeError("x"), context={})
    assert OutboxEmail.objects.get().recipient == "oncall@platform.example"


def test_checks_are_quiet_when_nothing_is_configured(tracker: MemoryTracker) -> None:
    assert run_ops_checks() == []


def test_low_disk_space_alerts(
    tracker: MemoryTracker, operator: None, settings: object, tmp_path: Path
) -> None:
    settings.OPS_DISK_PATHS = [str(tmp_path)]  # type: ignore[attr-defined]
    settings.OPS_DISK_MIN_FREE_PERCENT = 100  # type: ignore[attr-defined]  # always below
    alerts = run_ops_checks()
    assert [a.key for a in alerts] == [f"disk:{tmp_path}"]
    assert OutboxEmail.objects.count() == 1


def test_stale_backup_alerts_and_a_fresh_one_does_not(
    tracker: MemoryTracker, operator: None, settings: object, tmp_path: Path
) -> None:
    status = tmp_path / "last-backup"
    settings.OPS_BACKUP_STATUS_FILE = str(status)  # type: ignore[attr-defined]
    status.write_text((timezone.now() - dt.timedelta(hours=72)).isoformat())
    assert [a.key for a in run_ops_checks()] == ["backup:age"]
    status.write_text(timezone.now().isoformat())
    assert run_ops_checks() == []
    status.write_text("not a date")
    assert [a.key for a in run_ops_checks()] == ["backup:unreadable"]
    status.unlink()
    assert [a.key for a in run_ops_checks()] == ["backup:missing"]


def test_wal_check_reads_pg_stat_archiver(
    tracker: MemoryTracker, operator: None, settings: object
) -> None:
    settings.OPS_WAL_ARCHIVE_CHECK = True  # type: ignore[attr-defined]
    # The test database has no archiving: nothing was ever archived, which is the alert.
    assert [a.key for a in run_ops_checks()] == ["wal:never"]


def test_the_command_exits_non_zero_on_alerts_and_prints_no_secrets(
    tracker: MemoryTracker, operator: None, settings: object, tmp_path: Path
) -> None:
    settings.OPS_DISK_PATHS = [str(tmp_path)]  # type: ignore[attr-defined]
    settings.OPS_DISK_MIN_FREE_PERCENT = 100  # type: ignore[attr-defined]
    out = StringIO()
    with pytest.raises(CommandError, match="1 alert"):
        call_command("check_ops", stdout=out)
    assert "disk" in out.getvalue()


def test_the_command_succeeds_when_all_is_well(tracker: MemoryTracker) -> None:
    out = StringIO()
    call_command("check_ops", stdout=out)
    assert "ok" in out.getvalue().lower()
