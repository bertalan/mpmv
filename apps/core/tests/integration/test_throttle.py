"""CORE.5: throttle counters stored in PostgreSQL, shared by every worker process."""

import datetime as dt

import pytest
from django.utils import timezone

from apps.core.models import Throttle
from apps.core.services import (
    clear_throttle,
    purge_expired_throttles,
    record_throttle_failure,
    throttle_state,
)
from apps.core.tasks import purge_throttles
from apps.core.tests.factories import ThrottleFactory, utc
from apps.core.throttling import BackoffPolicy, ThrottleState

pytestmark = pytest.mark.django_db

T0 = utc(2027, 3, 1, 12)
POLICY = BackoffPolicy(
    free_attempts=2,
    first_lock=dt.timedelta(minutes=1),
    max_lock=dt.timedelta(minutes=10),
    forget_after=dt.timedelta(hours=1),
)


def test_unknown_key_has_a_clean_state() -> None:
    assert throttle_state("login:ip:203.0.113.7", now=T0) == ThrottleState()


def test_failures_are_counted_in_one_row_per_key() -> None:
    before, after = record_throttle_failure("login:ip:203.0.113.7", POLICY, now=T0)
    assert before == ThrottleState()
    assert after.failures == 1
    _, after = record_throttle_failure("login:ip:203.0.113.7", POLICY, now=T0)
    assert after.failures == 2
    assert after.is_locked(T0)
    row = Throttle.objects.get(key="login:ip:203.0.113.7")
    assert row.failures == 2
    assert row.locked_until == T0 + dt.timedelta(minutes=1)
    assert throttle_state("login:ip:203.0.113.7", now=T0) == after


def test_an_expired_row_reads_as_a_clean_state() -> None:
    ThrottleFactory.create(key="login:ip:198.51.100.1", failures=9, expires_at=T0)
    assert throttle_state("login:ip:198.51.100.1", now=T0) == ThrottleState()


def test_clear_throttle_forgets_the_key() -> None:
    row = ThrottleFactory.create(key="login:account:a@example.com", failures=3)
    clear_throttle(row.key)
    assert not Throttle.objects.filter(key=row.key).exists()
    clear_throttle(row.key)  # idempotent


def test_purge_removes_only_expired_rows() -> None:
    ThrottleFactory.create(key="old", expires_at=T0 - dt.timedelta(seconds=1))
    ThrottleFactory.create(key="current", expires_at=T0 + dt.timedelta(seconds=1))
    assert purge_expired_throttles(now=T0) == 1
    assert list(Throttle.objects.values_list("key", flat=True)) == ["current"]


def test_the_nightly_job_purges_expired_rows() -> None:
    now = timezone.now()
    ThrottleFactory.create(key="stale", expires_at=now - dt.timedelta(minutes=1))
    ThrottleFactory.create(key="fresh", expires_at=now + dt.timedelta(hours=1))
    purge_throttles.func(timestamp=int(now.timestamp()))
    assert list(Throttle.objects.values_list("key", flat=True)) == ["fresh"]
