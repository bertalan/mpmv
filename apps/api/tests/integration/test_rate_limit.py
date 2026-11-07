"""CORE.12 done-when: two app processes share one counter; the request over the limit gets 429
with `Retry-After`; another key is not affected.

Counters live in PostgreSQL (`ApiRateCounter`), the cache every app process shares. A second
process is modelled by a thread, which Django gives its own database connection; those tests
use `transaction=True` so each connection sees the other's committed rows.

HTTP tests use per-day limits, so a window boundary cannot fall in the middle of a test.
"""

import datetime as dt
import threading
from collections.abc import Callable

import pytest
from django.db import connection
from django.test import Client
from pytest_django.fixtures import SettingsWrapper

from apps.api.engine import KEY_WIDE_BUCKET
from apps.api.models import ApiMutationLog, ApiRateCounter
from apps.api.services import count_api_call
from apps.api.tests.factories import ApiClientFactory, ApiRateCounterFactory

V1 = pytest.mark.urls("apps.api.tests.integration.urls_v1")
PLAIN = pytest.mark.urls("apps.api.tests.integration.urls")

ME = "/api/v1-test/me"
ECHO = "/api/v1-test/test/echo"
PROTECTED = "/protected/catalog"
NOON = dt.datetime(2026, 11, 7, 12, 0, tzinfo=dt.UTC)


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _in_thread[T](work: Callable[[], T]) -> list[T | BaseException]:
    """Run `work` on its own database connection, as another app process would; wait for it."""
    outcome: list[T | BaseException] = []

    def run() -> None:
        try:
            outcome.append(work())
        except BaseException as exc:
            outcome.append(exc)
        finally:
            connection.close()

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(timeout=30)
    assert not thread.is_alive(), "the other process did not finish"
    return outcome


# --- Service


@pytest.mark.django_db
def test_calls_are_counted_per_key_and_the_one_over_the_limit_is_refused(
    settings: SettingsWrapper,
) -> None:
    settings.API_RATE_LIMIT = "2/minute"
    api_client = ApiClientFactory.create()
    now = NOON + dt.timedelta(seconds=15)
    decisions = [count_api_call(api_client, None, now=now) for _ in range(3)]
    assert [d.allowed for d in decisions] == [True, True, False]
    assert [d.remaining for d in decisions] == [1, 0, 0]
    assert decisions[2].retry_after == 45
    counter = ApiRateCounter.objects.get(client=api_client, bucket=KEY_WIDE_BUCKET)
    assert (counter.window_start, counter.hits) == (NOON, 3)


@pytest.mark.django_db
def test_a_new_window_starts_from_zero(settings: SettingsWrapper) -> None:
    settings.API_RATE_LIMIT = "1/minute"
    api_client = ApiClientFactory.create()
    assert count_api_call(api_client, None, now=NOON).allowed
    assert not count_api_call(api_client, None, now=NOON + dt.timedelta(seconds=59)).allowed
    assert count_api_call(api_client, None, now=NOON + dt.timedelta(seconds=60)).allowed


@pytest.mark.django_db
def test_a_late_call_from_the_previous_window_counts_in_the_current_one(
    settings: SettingsWrapper,
) -> None:
    """A process whose request started before the boundary must not reset the newer window."""
    settings.API_RATE_LIMIT = "5/minute"
    counter = ApiRateCounterFactory.create(window_start=NOON, hits=5)
    late = count_api_call(counter.client, None, now=NOON - dt.timedelta(seconds=1))
    assert not late.allowed
    counter.refresh_from_db()
    assert (counter.window_start, counter.hits) == (NOON, 6)


@pytest.mark.django_db
def test_another_key_is_not_affected_by_a_key_over_its_limit(settings: SettingsWrapper) -> None:
    settings.API_RATE_LIMIT = "1/minute"
    busy, quiet = ApiClientFactory.create_batch(2)
    count_api_call(busy, None, now=NOON)
    assert not count_api_call(busy, None, now=NOON).allowed
    assert count_api_call(quiet, None, now=NOON).allowed


@pytest.mark.django_db(transaction=True)
def test_concurrent_processes_never_lose_a_count(settings: SettingsWrapper) -> None:
    settings.API_RATE_LIMIT = "50/minute"
    api_client = ApiClientFactory.create()
    barrier = threading.Barrier(4)

    def burst() -> list[bool]:
        barrier.wait(timeout=10)
        try:
            return [count_api_call(api_client, None, now=NOON).allowed for _ in range(20)]
        finally:
            connection.close()

    results: list[list[bool] | BaseException] = []
    threads = [threading.Thread(target=lambda: results.append(burst())) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert len(results) == 4
    assert all(isinstance(r, list) for r in results), results
    allowed = sum(sum(r) for r in results if isinstance(r, list))
    assert allowed == 50  # exactly the limit, whatever the interleaving
    assert ApiRateCounter.objects.get(client=api_client).hits == 80


@pytest.mark.django_db
def test_a_scope_with_its_own_limit_does_not_use_up_the_key_wide_budget(
    settings: SettingsWrapper,
) -> None:
    settings.API_RATE_LIMIT = "2/minute"
    settings.API_RATE_LIMIT_SCOPES = {"catalog:write": "1/minute"}
    api_client = ApiClientFactory.create()
    assert count_api_call(api_client, "catalog:write", now=NOON).allowed
    assert not count_api_call(api_client, "catalog:write", now=NOON).allowed
    assert count_api_call(api_client, "catalog:read", now=NOON).allowed
    assert count_api_call(api_client, None, now=NOON).allowed
    assert not count_api_call(api_client, "catalog:read", now=NOON).allowed


@pytest.mark.django_db
def test_a_key_listed_in_the_settings_gets_its_own_limit(settings: SettingsWrapper) -> None:
    importer, other = ApiClientFactory.create_batch(2)
    settings.API_RATE_LIMIT = "1/minute"
    settings.API_RATE_LIMIT_CLIENTS = {str(importer.public_id): "3/minute"}
    assert [count_api_call(importer, None, now=NOON).allowed for _ in range(4)] == [
        True,
        True,
        True,
        False,
    ]
    assert [count_api_call(other, None, now=NOON).allowed for _ in range(2)] == [True, False]


# --- HTTP (API v1 and plain views)


def _assert_rate_limited(response: object) -> dict[str, object]:
    assert response.status_code == 429  # type: ignore[attr-defined]
    assert response["Content-Type"] == "application/problem+json"  # type: ignore[index]
    retry_after = int(response["Retry-After"])  # type: ignore[index]
    assert 0 < retry_after <= 86400
    body: dict[str, object] = response.json()  # type: ignore[attr-defined]
    assert body["status"] == 429
    assert body["code"] == "rate_limited"
    assert body["type"] == "urn:mpmv:problem:rate_limited"
    assert "Retry-After" in str(body["detail"])
    return body


@V1
@pytest.mark.django_db
def test_the_request_over_the_limit_is_a_429_problem_with_retry_after(
    client: Client, settings: SettingsWrapper
) -> None:
    settings.API_RATE_LIMIT = "2/day"
    _, key = ApiClientFactory.create_with_key()
    assert [client.get(ME, headers=_bearer(key)).status_code for _ in range(2)] == [200, 200]
    response = client.get(ME, headers=_bearer(key))
    body = _assert_rate_limited(response)
    assert body["instance"] == ME
    assert body["request_id"] == response["X-Request-ID"]


@V1
@pytest.mark.django_db
def test_another_key_still_gets_through(client: Client, settings: SettingsWrapper) -> None:
    settings.API_RATE_LIMIT = "1/day"
    _, busy = ApiClientFactory.create_with_key()
    _, quiet = ApiClientFactory.create_with_key()
    client.get(ME, headers=_bearer(busy))
    _assert_rate_limited(client.get(ME, headers=_bearer(busy)))
    assert client.get(ME, headers=_bearer(quiet)).status_code == 200


@V1
@pytest.mark.django_db(transaction=True)
def test_two_app_processes_share_one_counter(client: Client, settings: SettingsWrapper) -> None:
    settings.API_RATE_LIMIT = "3/day"
    _, key = ApiClientFactory.create_with_key()
    other_process = _in_thread(
        lambda: [Client().get(ME, headers=_bearer(key)).status_code for _ in range(3)]
    )
    assert other_process == [[200, 200, 200]]
    _assert_rate_limited(client.get(ME, headers=_bearer(key)))


@V1
@pytest.mark.django_db
def test_requests_refused_before_the_limit_check_do_not_use_up_the_budget(
    client: Client, settings: SettingsWrapper
) -> None:
    settings.API_RATE_LIMIT = "1/day"
    api_client, key = ApiClientFactory.create_with_key(scopes=["reports:read"])
    for _ in range(3):
        response = client.post(ECHO, {}, content_type="application/json", headers=_bearer(key))
        assert response.status_code == 403
    assert client.get(ME, headers=_bearer(key)).status_code == 200
    assert not ApiRateCounter.objects.filter(client=api_client, hits__gt=1).exists()


@V1
@pytest.mark.django_db
def test_a_rate_limited_mutating_call_is_in_the_mutation_log(
    client: Client, settings: SettingsWrapper
) -> None:
    settings.API_RATE_LIMIT = "1/day"
    api_client, key = ApiClientFactory.create_with_key(scopes=["test:write"])
    payload = {"name": "Widget", "quantity": 1}
    first = client.post(ECHO, payload, content_type="application/json", headers=_bearer(key))
    assert first.status_code == 200
    _assert_rate_limited(
        client.post(ECHO, payload, content_type="application/json", headers=_bearer(key))
    )
    logged = ApiMutationLog.objects.filter(client_public_id=api_client.public_id)
    assert sorted(logged.values_list("status_code", flat=True)) == [200, 429]


@PLAIN
@pytest.mark.django_db
def test_plain_views_behind_require_api_key_are_limited_too(
    client: Client, settings: SettingsWrapper
) -> None:
    settings.API_RATE_LIMIT = "1/day"
    _, key = ApiClientFactory.create_with_key(scopes=["catalog:write"])
    assert client.get(PROTECTED, headers=_bearer(key)).status_code == 200
    body = _assert_rate_limited(client.get(PROTECTED, headers=_bearer(key)))
    assert body["instance"] == PROTECTED
