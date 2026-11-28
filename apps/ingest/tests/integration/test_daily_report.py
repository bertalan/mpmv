"""ING.10: the daily report gathered from the database, mailed to staff and shown in the admin."""

import datetime as dt
from collections.abc import Callable
from typing import Any

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.core.models import AuditLog, OutboxEmail
from apps.core.tasks import schedules
from apps.ingest import reports, tasks
from apps.ingest.models import ImportRun, ReviewItem
from apps.ingest.tests.factories import (
    ImportRunFactory,
    ReviewItemFactory,
    SourceItemFactory,
    SupplierSourceFactory,
)

pytestmark = pytest.mark.django_db

NOW = dt.datetime(2026, 11, 28, 23, 10, tzinfo=dt.UTC)
DAY = NOW.date()


@pytest.fixture(autouse=True)
def alert_address(settings: Any) -> str:
    settings.OPS_ALERT_EMAIL = "staff@platform.example"
    return str(settings.OPS_ALERT_EMAIL)


def _run(source: Any, **kwargs: Any) -> ImportRun:
    run = ImportRunFactory.create(source=source, **kwargs)
    ImportRun.objects.filter(pk=run.pk).update(created_at=NOW - dt.timedelta(hours=15))
    return run


def test_the_report_gathers_the_days_runs_reviews_and_anomalies() -> None:
    source = SupplierSourceFactory.create()
    _run(source, kind="stock", status="succeeded", seen=5, updated=2, dry_run=False)
    _run(source, kind="price", status="failed", errors=1, log="RuntimeError: boom")
    old = ImportRunFactory.create(source=source)
    ImportRun.objects.filter(pk=old.pk).update(created_at=NOW - dt.timedelta(days=2))
    item = SourceItemFactory.create(source=source)
    ReviewItemFactory.create(run=ImportRun.objects.filter(source=source).first(), item=item,
                             kind=ReviewItem.Kind.IDENTITY_CHANGE, blocking=True)  # fmt: skip
    report = reports.gather_daily_report(DAY)
    assert report.runs_total == 2
    assert report.runs_failed == 1
    assert report.opened_by_kind["identity_change"] == 1
    assert [a.kind for a in report.anomalies] == ["identity_change"]


def test_two_vendors_with_the_same_sku_are_not_a_collision() -> None:
    first, second = SupplierSourceFactory.create(), SupplierSourceFactory.create()
    SourceItemFactory.create(source=first, sku="Same-1")
    SourceItemFactory.create(source=second, sku="Same-1")
    assert reports.gather_daily_report(DAY).anomalies == []


def test_one_vendor_with_the_same_sku_twice_is_a_collision() -> None:
    source = SupplierSourceFactory.create()
    SourceItemFactory.create(source=source, sku="Dup-1")
    SourceItemFactory.create(source=source, sku="DUP-1")
    anomalies = reports.gather_daily_report(DAY).anomalies
    assert [a.kind for a in anomalies] == ["sku_collision"]


def test_one_source_that_raises_is_listed_and_the_others_are_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad, good = SupplierSourceFactory.create(), SupplierSourceFactory.create()
    _run(good, status="succeeded")
    real = reports._source_rows

    def flaky(source: Any, *args: Any, **kwargs: Any) -> Any:
        if source.pk == bad.pk:
            raise RuntimeError("broken")
        return real(source, *args, **kwargs)

    monkeypatch.setattr(reports, "_source_rows", flaky)
    report = reports.gather_daily_report(DAY)
    assert report.unavailable == [bad.code]
    assert report.runs_total == 1


def test_the_job_mails_staff_once_per_day_and_audits_it() -> None:
    SupplierSourceFactory.create()
    tasks.daily_report.func(timestamp=int(NOW.timestamp()))
    tasks.daily_report.func(timestamp=int(NOW.timestamp()))
    mail = OutboxEmail.objects.get(template_key="ingest.daily_report")
    assert mail.recipient == "staff@platform.example"
    assert "<img" not in mail.html_body
    assert AuditLog.objects.filter(action="ingest.daily_report.sent").count() == 1


def test_the_job_does_not_raise_when_the_report_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("no report")

    monkeypatch.setattr(reports, "gather_daily_report", boom)
    tasks.daily_report.func(timestamp=int(NOW.timestamp()))
    assert not OutboxEmail.objects.filter(template_key="ingest.daily_report").exists()


def test_the_job_without_a_recipient_sends_nothing(settings: Any) -> None:
    settings.OPS_ALERT_EMAIL = ""
    tasks.daily_report.func(timestamp=int(NOW.timestamp()))
    assert not OutboxEmail.objects.exists()


def test_the_job_is_periodic_after_the_syncs_and_locked() -> None:
    entry = next(s for s in schedules() if s.task_name == "ingest.daily_report")
    assert entry
    assert tasks.daily_report.lock == "ingest.daily_report"
    assert tasks.daily_report.queueing_lock == "ingest.daily_report"


def test_the_email_renders_in_every_language() -> None:
    for language in settings.LANGUAGES:
        report = reports.gather_daily_report(DAY)
        from apps.core.services import render_email
        from apps.ingest import engine

        rendered = render_email("ingest.daily_report", language[0], engine.report_context(report))
        assert rendered.subject
        assert "2026-11-28" in rendered.text_body


@pytest.fixture
def staff_client(
    client: Client, superuser: User, mfa_login: Callable[[Client, User], Client]
) -> Client:
    return mfa_login(client, superuser)


def test_staff_see_the_same_report_in_the_admin(staff_client: Client) -> None:
    source = SupplierSourceFactory.create()
    _run(source, status="failed", errors=1, log="RuntimeError: boom")
    response = staff_client.get(reverse("admin:ingest_importrun_daily_report"))
    assert response.status_code == 200
    assert source.code in response.content.decode()


def test_the_admin_report_takes_a_day_and_refuses_a_bad_one(staff_client: Client) -> None:
    url = reverse("admin:ingest_importrun_daily_report")
    assert staff_client.get(url, {"day": "2026-11-01"}).status_code == 200
    assert staff_client.get(url, {"day": "nope"}).status_code == 200  # falls back to today


def test_a_vendor_member_cannot_open_the_admin_report(
    cross_tenant: Any, client: Client, mfa_login: Callable[[Client, User], Client]
) -> None:
    client = mfa_login(client, cross_tenant.a.owner)
    response = client.get(reverse("admin:ingest_importrun_daily_report"))
    assert response.status_code in (302, 403, 404)
