"""ING.8: the scheduled stock and price jobs."""

import pytest

from apps.core.tasks import schedules
from apps.ingest import tasks
from apps.ingest.models import ImportRun
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
