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
from apps.api.models import ApiMutationLog
from apps.api.services import MUTATION_LOG_RETENTION_MONTHS, purge_mutation_log
from apps.api.tests.factories import ApiMutationLogFactory
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
