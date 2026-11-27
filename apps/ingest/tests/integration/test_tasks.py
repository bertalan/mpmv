"""ING.8: the scheduled stock and price jobs."""

from typing import Any

import pytest

from apps.core.tasks import schedules
from apps.ingest import services, sync, tasks
from apps.ingest.models import ImportRun, SupplierSource
from apps.ingest.tests.factories import SupplierSourceFactory

pytestmark = pytest.mark.django_db


def test_the_stock_job_runs_every_active_source_and_skips_the_inactive() -> None:
    SupplierSourceFactory.create(is_active=True)
    SupplierSourceFactory.create(is_active=False)
    tasks.stock_sync.func(timestamp=0)
    runs = ImportRun.objects.all()
    assert [r.kind for r in runs] == ["stock"]
    assert runs[0].status == "succeeded"
    assert runs[0].dry_run is True  # no per-source write flag


def test_the_price_job_queues_a_price_run() -> None:
    SupplierSourceFactory.create()
    tasks.price_sync.func(timestamp=0)
    assert ImportRun.objects.get().kind == "price"


def test_both_jobs_are_scheduled_daily() -> None:
    names = {schedule.task_name for schedule in schedules()}
    assert {"ingest.stock_sync", "ingest.price_sync"} <= names


def test_each_job_holds_a_lock_so_a_second_run_is_skipped() -> None:
    assert tasks.stock_sync.lock == "ingest.stock_sync"
    assert tasks.stock_sync.queueing_lock == "ingest.stock_sync"
    assert tasks.price_sync.lock == "ingest.price_sync"


def test_a_failing_source_does_not_stop_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    first = SupplierSourceFactory.create()
    second = SupplierSourceFactory.create()
    real = sync.sync_stock

    def flaky(run: ImportRun, **kwargs: Any) -> sync.SyncResult:
        if run.source_id == first.pk:
            raise RuntimeError("supplier data broke")
        return real(run, **kwargs)

    monkeypatch.setattr(sync, "sync_stock", flaky)
    tasks.stock_sync.func(timestamp=0)
    failed = ImportRun.objects.get(source=first)
    ok = ImportRun.objects.get(source=second)
    assert failed.status == "failed"
    assert "RuntimeError: supplier data broke" in failed.log
    assert ok.status == "succeeded"


def test_a_source_that_cannot_be_queued_does_not_stop_the_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = SupplierSourceFactory.create()
    second = SupplierSourceFactory.create()
    real = services.queue_run

    def flaky(source: SupplierSource, *args: Any, **kwargs: Any) -> Any:
        if source.pk == first.pk:
            raise RuntimeError("queue broke")
        return real(source, *args, **kwargs)

    monkeypatch.setattr("apps.ingest.sync.queue_run", flaky)
    tasks.stock_sync.func(timestamp=0)
    assert not ImportRun.objects.filter(source=first).exists()
    assert ImportRun.objects.get(source=second).status == "succeeded"
