"""CORE.29 (owner decision Q-14): the API mutation log is kept 12 months, then purged each night.

The log holds the call (key id, endpoint, method, status, time, IP address), never a body, a
token or personal data; a test pins its columns so a new one needs a conscious decision.
"""

import datetime as dt
from unittest import mock

import pytest
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from apps.api import tasks as api_tasks
from apps.api.engine import months_before
from apps.api.models import ApiMutationLog
from apps.api.services import MUTATION_LOG_RETENTION_MONTHS, purge_mutation_log
from apps.api.tests.factories import ApiMutationLogFactory
from apps.core.models import AuditLog
from apps.core.tasks import schedules

pytestmark = pytest.mark.django_db

# The stored columns: identity and time, the key, the endpoint, the outcome, the address.
# No request or response body, no query string, no header, no token, no user data.
ALLOWED_COLUMNS = {
    "id",
    "public_id",
    "created_at",
    "request_id",
    "client_public_id",
    "client_name",
    "key_prefix",
    "method",
    "path",
    "route",
    "dry_run",
    "status_code",
    "ip",
    "duration_ms",
}


def _logged_at(moment: dt.datetime) -> ApiMutationLog:
    with mock.patch("django.utils.timezone.now", return_value=moment):
        return ApiMutationLogFactory.create()


def test_the_log_stores_only_the_allowed_columns() -> None:
    columns = {field.name for field in ApiMutationLog._meta.get_fields()}
    assert columns == ALLOWED_COLUMNS


def test_the_log_has_no_free_text_column_that_could_hold_a_body() -> None:
    from django.db import models

    texts = [
        f.name
        for f in ApiMutationLog._meta.get_fields()
        if isinstance(f, models.TextField | models.JSONField | models.BinaryField)
    ]
    assert texts == []


def test_the_purge_removes_rows_older_than_12_months_and_keeps_newer_ones() -> None:
    now = timezone.now()
    old = _logged_at(now - dt.timedelta(days=366 + 2))
    just_over = _logged_at(now - dt.timedelta(days=366 + 1))
    recent = _logged_at(now - dt.timedelta(days=300))
    fresh = _logged_at(now)
    assert MUTATION_LOG_RETENTION_MONTHS == 12

    assert purge_mutation_log(now=now) == 2

    assert set(ApiMutationLog.objects.values_list("pk", flat=True)) == {recent.pk, fresh.pk}
    assert old.pk not in ApiMutationLog.objects.values_list("pk", flat=True)
    assert just_over.pk not in ApiMutationLog.objects.values_list("pk", flat=True)


def test_the_purge_is_idempotent() -> None:
    now = timezone.now()
    _logged_at(now - dt.timedelta(days=400))
    assert purge_mutation_log(now=now) == 1
    assert purge_mutation_log(now=now) == 0


def test_the_database_still_refuses_to_delete_a_recent_row_or_update_any() -> None:
    recent = ApiMutationLogFactory.create()
    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("DELETE FROM api_apimutationlog WHERE id = %s", [recent.pk])
    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("UPDATE api_apimutationlog SET status_code = 200")
    assert ApiMutationLog.objects.count() == 1


def test_the_purge_is_a_nightly_job() -> None:
    nightly = {s.task_name: s for s in schedules()}["api.purge_mutation_log"]
    assert nightly.cron == "12 4 * * *"
    with mock.patch("apps.api.tasks.services.purge_mutation_log", return_value=0) as purge:
        api_tasks.purge_mutation_log.func(timestamp=0)
    purge.assert_called_once()


def test_the_purge_job_is_periodic_and_locked() -> None:
    assert api_tasks.purge_mutation_log.lock == "api.purge_mutation_log"
    assert api_tasks.purge_mutation_log.queueing_lock == "api.purge_mutation_log"
    assert "api.purge_mutation_log" in {s.task_name for s in schedules()}


def test_the_job_does_not_raise_when_the_delete_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    boom = DatabaseError("simulated failure")
    with mock.patch("apps.api.tasks.services.purge_mutation_log", side_effect=boom):
        api_tasks.purge_mutation_log.func(timestamp=0)  # must not raise
    assert any("api.purge_mutation_log failed" in r.getMessage() for r in caplog.records)
    assert any(r.exc_info for r in caplog.records)


def test_a_row_just_inside_12_months_stays_and_one_just_outside_goes() -> None:
    now = dt.datetime.now(dt.UTC).replace(microsecond=0)
    cutoff = months_before(now, 12)
    inside = _logged_at(cutoff + dt.timedelta(minutes=1))
    outside = _logged_at(cutoff - dt.timedelta(minutes=1))

    assert purge_mutation_log(now=now) == 1

    assert list(ApiMutationLog.objects.values_list("pk", flat=True)) == [inside.pk]
    assert not ApiMutationLog.objects.filter(pk=outside.pk).exists()


def test_a_row_exactly_at_the_cutoff_stays_and_one_microsecond_older_goes() -> None:
    # The purge filters with `created_at < cutoff`: a row stamped exactly at the cutoff is kept.
    now = dt.datetime.now(dt.UTC)
    cutoff = months_before(now, 12)
    at_cutoff = _logged_at(cutoff)
    older = _logged_at(cutoff - dt.timedelta(microseconds=1))

    assert purge_mutation_log(now=now) == 1

    assert list(ApiMutationLog.objects.values_list("pk", flat=True)) == [at_cutoff.pk]
    assert not ApiMutationLog.objects.filter(pk=older.pk).exists()


def _old_rows(count: int) -> list[ApiMutationLog]:
    old = timezone.now() - dt.timedelta(days=400)
    return [_logged_at(old) for _ in range(count)]


def test_the_purge_deletes_in_batches_each_in_its_own_transaction() -> None:
    _old_rows(5)
    recent = ApiMutationLogFactory.create()
    with (
        mock.patch("apps.api.services.MUTATION_LOG_BATCH_SIZE", 2),
        mock.patch("apps.api.services.transaction") as txn,
    ):
        txn.atomic.side_effect = transaction.atomic  # the real savepoint, counted
        assert purge_mutation_log() == 5
    # 5 rows in batches of 2: three batches that delete (2, 2, 1); the last one is short, so the
    # loop ends without a fourth look. Each batch is its own atomic block.
    assert txn.atomic.call_count == 3
    assert list(ApiMutationLog.objects.values_list("pk", flat=True)) == [recent.pk]


def test_one_run_is_bounded_and_the_next_run_continues() -> None:
    _old_rows(5)
    with (
        mock.patch("apps.api.services.MUTATION_LOG_BATCH_SIZE", 2),
        mock.patch("apps.api.services.MUTATION_LOG_MAX_BATCHES", 2),
    ):
        assert purge_mutation_log() == 4
        assert ApiMutationLog.objects.count() == 1
        assert purge_mutation_log() == 1
    assert ApiMutationLog.objects.count() == 0


def test_a_run_that_deletes_rows_writes_one_audit_entry_with_counts_only() -> None:
    now = dt.datetime.now(dt.UTC)
    _old_rows(3)
    ApiMutationLogFactory.create()
    with mock.patch("apps.api.services.MUTATION_LOG_BATCH_SIZE", 2):
        assert purge_mutation_log(now=now) == 3
    entry = AuditLog.objects.get(action="api.mutation_log.retention_purged")
    assert entry.after == {"deleted": 3, "cutoff": months_before(now, 12).isoformat()}
    assert entry.before is None
    assert entry.target_model == "api.apimutationlog"
    assert entry.source == AuditLog.Source.TASK
    assert entry.ip in (None, "")


def test_a_run_that_deletes_nothing_writes_no_audit_entry() -> None:
    ApiMutationLogFactory.create()
    assert purge_mutation_log() == 0
    assert not AuditLog.objects.filter(action="api.mutation_log.retention_purged").exists()
