"""ORD.10: the periodic check raises the OPS.2 alert and the command reports it."""

import datetime as dt
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.core.models import OutboxEmail
from apps.payments.engine import ALERT_KEY, PaidPayment
from apps.payments.services import check_payments_without_transfer

pytestmark = pytest.mark.django_db

NOW = dt.datetime(2027, 3, 1, 12, 0, tzinfo=dt.UTC)


class FakeSource:
    def __init__(self, rows: list[PaidPayment]) -> None:
        self.rows = rows
        self.since: dt.datetime | None = None

    def succeeded_payments(self, *, since: dt.datetime) -> list[PaidPayment]:
        self.since = since
        return self.rows


def late(now: dt.datetime = NOW) -> PaidPayment:
    return PaidPayment("pay_1", now - dt.timedelta(hours=3), has_transfer=False)


def test_a_late_payment_raises_the_alert_and_queues_the_staff_mail(
    settings: object,
) -> None:
    settings.OPS_ALERT_EMAIL = "ops@example.com"  # type: ignore[attr-defined]
    alert = check_payments_without_transfer(FakeSource([late()]), now=NOW)
    assert alert is not None
    assert alert.key == ALERT_KEY
    assert OutboxEmail.objects.filter(recipient="ops@example.com").count() == 1


def test_the_mail_is_sent_once_per_repeat_window(settings: object) -> None:
    settings.OPS_ALERT_EMAIL = "ops@example.com"  # type: ignore[attr-defined]
    source = FakeSource([late()])
    check_payments_without_transfer(source, now=NOW)
    check_payments_without_transfer(source, now=NOW + dt.timedelta(minutes=5))
    assert OutboxEmail.objects.count() == 1


def test_a_matched_payment_raises_nothing(settings: object) -> None:
    settings.OPS_ALERT_EMAIL = "ops@example.com"  # type: ignore[attr-defined]
    done = PaidPayment("pay_2", NOW - dt.timedelta(hours=3), has_transfer=True)
    assert check_payments_without_transfer(FakeSource([done]), now=NOW) is None
    assert OutboxEmail.objects.count() == 0


def test_the_source_is_asked_for_the_lookback_window() -> None:
    source = FakeSource([])
    check_payments_without_transfer(source, now=NOW)
    assert source.since == NOW - dt.timedelta(days=30)


def test_the_command_is_quiet_until_a_payment_source_is_wired() -> None:
    out = StringIO()
    call_command("check_payments", stdout=out)
    assert "ok" in out.getvalue()


def test_the_command_fails_when_the_check_alerts(monkeypatch: pytest.MonkeyPatch) -> None:
    source = FakeSource([late(timezone.now())])
    monkeypatch.setattr("apps.payments.services.payment_source", lambda: source)
    with pytest.raises(CommandError):
        call_command("check_payments", stdout=StringIO())
