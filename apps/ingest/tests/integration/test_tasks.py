"""ING.8: the scheduled stock and price jobs."""

from decimal import Decimal
from typing import Any

import pytest

from apps.catalog.models import Product
from apps.catalog.tests.factories import ProductFactory
from apps.core.tasks import schedules
from apps.ingest import services, sync, tasks
from apps.ingest.models import ImportRun, SupplierSource
from apps.ingest.tests.factories import (
    ImportRunFactory,
    SourceItemFactory,
    SourceStateFactory,
    SupplierSourceFactory,
)

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


# --- job guard (lane-a review of #1132)


def _item_with_stock(source: SupplierSource, qty: int, product_qty: int = 0) -> Product:
    product = ProductFactory.create(vendor=source.vendor, stock_qty=product_qty)
    item = SourceItemFactory.create(source=source, product=product)
    SourceStateFactory.create(
        item=item, state={"kind": "simple", "sku": item.sku}, stock_status=1, stock_quantity=qty
    )
    return product


def test_a_write_enabled_source_is_applied_through_the_job_with_a_real_run() -> None:
    source = SupplierSourceFactory.create(write_enabled=True)
    product = _item_with_stock(source, 7)
    tasks.stock_sync.func(timestamp=0)
    product.refresh_from_db()
    run = ImportRun.objects.get()
    assert product.stock_qty == 7
    assert (run.status, run.dry_run, run.updated) == ("succeeded", False, 1)


def test_the_price_job_closes_a_dry_run_without_writing() -> None:
    source = SupplierSourceFactory.create(write_enabled=False)
    product = ProductFactory.create(vendor=source.vendor, price_net=Decimal("10.00"))
    item = SourceItemFactory.create(source=source, product=product)
    SourceStateFactory.create(
        item=item, state={"kind": "simple", "sku": item.sku, "price": "11.00"}
    )
    tasks.price_sync.func(timestamp=0)
    run = ImportRun.objects.get()
    product.refresh_from_db()
    assert (run.kind, run.status, run.dry_run) == ("price", "succeeded", True)
    assert product.price_net == Decimal("10.00")


def test_two_vendors_with_the_same_sku_are_each_synced_on_their_own_product() -> None:
    a = SupplierSourceFactory.create(write_enabled=True)
    b = SupplierSourceFactory.create(write_enabled=True)
    prod_a = ProductFactory.create(vendor=a.vendor, sku="SAME-1", stock_qty=0)
    prod_b = ProductFactory.create(vendor=b.vendor, sku="SAME-1", stock_qty=0)
    item_a = SourceItemFactory.create(source=a, product=prod_a, sku="SAME-1")
    SourceStateFactory.create(
        item=item_a, state={"kind": "simple", "sku": "SAME-1"}, stock_status=1, stock_quantity=5
    )
    item_b = SourceItemFactory.create(source=b, product=prod_b, sku="SAME-1")
    SourceStateFactory.create(
        item=item_b, state={"kind": "simple", "sku": "SAME-1"}, stock_status=1, stock_quantity=9
    )
    tasks.stock_sync.func(timestamp=0)
    prod_a.refresh_from_db()
    prod_b.refresh_from_db()
    assert (prod_a.stock_qty, prod_b.stock_qty) == (5, 9)
    assert {r.source_id for r in ImportRun.objects.all()} == {a.pk, b.pk}


def test_a_failure_while_writing_rolls_the_source_back_and_ends_the_run_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = SupplierSourceFactory.create(write_enabled=True)
    product = _item_with_stock(source, 7)

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("catalog broke")

    monkeypatch.setattr("apps.ingest.sync.catalog_services.update_by_sku", boom)
    tasks.stock_sync.func(timestamp=0)  # does not raise
    product.refresh_from_db()
    run = ImportRun.objects.get()
    assert product.stock_qty == 0  # all or nothing: nothing half-written
    assert (run.status, run.updated, run.errors) == ("failed", 0, 1)


def test_failing_to_start_or_to_close_a_run_ends_it_failed_and_the_next_source_goes_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = SupplierSourceFactory.create()
    second = SupplierSourceFactory.create()
    real = sync.transition_run

    def flaky(run: ImportRun, target: str, **kwargs: Any) -> Any:
        if run.source_id == first.pk and target == "succeeded":
            raise RuntimeError("cannot close")
        return real(run, target, **kwargs)

    monkeypatch.setattr("apps.ingest.sync.transition_run", flaky)
    tasks.stock_sync.func(timestamp=0)
    assert ImportRun.objects.get(source=first).status == "failed"
    assert ImportRun.objects.get(source=second).status == "succeeded"


def test_a_source_whose_run_sync_raises_does_not_stop_the_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = SupplierSourceFactory.create()
    second = SupplierSourceFactory.create()
    real = sync.run_sync

    def flaky(source: SupplierSource, *args: Any, **kwargs: Any) -> Any:
        if source.pk == first.pk:
            raise RuntimeError("escaped the guard")
        return real(source, *args, **kwargs)

    monkeypatch.setattr(sync, "run_sync", flaky)
    tasks.price_sync.func(timestamp=0)
    assert not ImportRun.objects.filter(source=first).exists()
    assert ImportRun.objects.get(source=second).status == "succeeded"


def test_a_second_run_while_one_is_running_is_still_safe_and_the_job_is_locked() -> None:
    source = SupplierSourceFactory.create()
    ImportRunFactory.create(source=source, kind="stock", status="running")
    tasks.stock_sync.func(timestamp=0)
    statuses = sorted(ImportRun.objects.filter(source=source).values_list("status", flat=True))
    assert statuses == ["running", "succeeded"]  # the stuck row is left alone, not corrupted
    assert tasks.stock_sync.queueing_lock == "ingest.stock_sync"  # one queued at a time
