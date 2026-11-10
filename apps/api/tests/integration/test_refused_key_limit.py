"""CORE.20 done-when: refused API keys (401) are limited per client IP.

Over `API_REFUSED_KEY_RATE_LIMIT` refused answers in a rolling window, the IP gets 429 with
`Retry-After` for `API_REFUSED_KEY_BLOCK_SECONDS`, decided before any key lookup. Valid keys
never count. The IP is the one `apps.core.http.client_ip` resolves behind the trusted proxy,
bucketed like every other limit (`apps.core.throttling.ip_bucket`: IPv4 address, IPv6 /64).

Thresholds are read from the settings, so the tests hold for any default.
"""

import datetime as dt
import logging
from typing import Any

import pytest
from django.conf import settings as django_settings
from django.db import connection
from django.test import Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.api import tasks as api_tasks
from apps.api.engine import KEY_NAMESPACE, generate_key, parse_rate
from apps.api.models import ApiClient, ApiRefusedKey
from apps.api.services import (
    purge_expired_refused_keys,
    record_refused_key,
    refused_key_retry_after,
)
from apps.api.tests.factories import ApiClientFactory, ApiRefusedKeyFactory
from apps.core.models import Throttle
from apps.core.tasks import schedules

pytestmark = [pytest.mark.django_db, pytest.mark.urls("apps.api.tests.integration.urls_v1")]

ME = "/api/v1-test/me"
PROTECTED = "/protected/catalog"
IP = "203.0.113.77"
OTHER_IP = "198.51.100.77"
PROXY = "10.89.250.2"
BEHIND_PROXY = {"TRUSTED_PROXY_HEADER": "X-Real-IP", "TRUSTED_PROXIES": ["10.89.250.0/24"]}
PLAIN = pytest.mark.urls("apps.api.tests.integration.urls")
NOON = dt.datetime(2026, 11, 7, 12, 0, tzinfo=dt.UTC)


def _limit() -> int:
    return parse_rate(django_settings.API_REFUSED_KEY_RATE_LIMIT).limit


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _refused(client: Client, *, url: str = ME, **extra: Any) -> int:
    return client.get(url, headers=_bearer(generate_key().key), **extra).status_code


def _flood(client: Client, times: int, **extra: Any) -> list[int]:
    return [_refused(client, **extra) for _ in range(times)]


def _end_blocks() -> None:
    Throttle.objects.filter(key__startswith="api:refused-key:").update(
        locked_until=timezone.now() - dt.timedelta(seconds=1)
    )


@pytest.fixture
def three_a_minute(settings: SettingsWrapper) -> None:
    settings.API_REFUSED_KEY_RATE_LIMIT = "3/minute"


def test_the_default_is_30_refused_answers_a_minute_and_a_5_minute_block() -> None:
    rate = parse_rate(django_settings.API_REFUSED_KEY_RATE_LIMIT)
    assert (rate.limit, rate.period) == (30, 60)
    assert django_settings.API_REFUSED_KEY_BLOCK_SECONDS == 300


def test_over_the_limit_an_ip_gets_429_with_retry_after(client: Client) -> None:
    limit = _limit()
    assert _flood(client, limit, REMOTE_ADDR=IP) == [401] * limit
    response = client.get(ME, headers=_bearer(generate_key().key), REMOTE_ADDR=IP)
    assert response.status_code == 429
    assert response["Content-Type"] == "application/problem+json"
    retry_after = int(response["Retry-After"])
    assert retry_after >= django_settings.API_REFUSED_KEY_BLOCK_SECONDS - 2
    assert retry_after <= django_settings.API_REFUSED_KEY_BLOCK_SECONDS


@PLAIN
def test_the_plain_django_adapter_is_limited_too(client: Client) -> None:
    limit = _limit()
    statuses = [_refused(client, url=PROTECTED, REMOTE_ADDR=IP) for _ in range(limit + 1)]
    assert statuses == [401] * limit + [429]


@pytest.mark.usefixtures("three_a_minute")
def test_a_blocked_ip_is_answered_before_any_key_lookup(client: Client) -> None:
    _flood(client, 3, REMOTE_ADDR=IP)
    _, key = ApiClientFactory.create_with_key()
    with CaptureQueriesContext(connection) as queries:
        response = client.get(ME, headers=_bearer(key), REMOTE_ADDR=IP)
    assert response.status_code == 429
    table = ApiClient._meta.db_table
    assert not [q["sql"] for q in queries.captured_queries if table in q["sql"]]


@pytest.mark.usefixtures("three_a_minute")
def test_another_ip_is_not_affected(client: Client) -> None:
    _flood(client, 3, REMOTE_ADDR=IP)
    assert _refused(client, REMOTE_ADDR=IP) == 429
    assert _refused(client, REMOTE_ADDR=OTHER_IP) == 401


@pytest.mark.usefixtures("three_a_minute")
def test_valid_keys_never_count(client: Client) -> None:
    _, key = ApiClientFactory.create_with_key()
    for _ in range(10):
        assert client.get(ME, headers=_bearer(key), REMOTE_ADDR=IP).status_code == 200
    assert _flood(client, 3, REMOTE_ADDR=IP) == [401, 401, 401]


@pytest.mark.usefixtures("three_a_minute")
def test_a_valid_key_works_again_once_the_block_ends(client: Client) -> None:
    """During the block every call from the IP gets 429: the check runs before the key is read.
    Once the block ends the IP starts afresh, valid keys included."""
    _, key = ApiClientFactory.create_with_key()
    _flood(client, 3, REMOTE_ADDR=IP)
    assert client.get(ME, headers=_bearer(key), REMOTE_ADDR=IP).status_code == 429
    _end_blocks()
    assert client.get(ME, headers=_bearer(key), REMOTE_ADDR=IP).status_code == 200
    assert _flood(client, 3, REMOTE_ADDR=IP) == [401, 401, 401]  # the old count is gone
    assert _refused(client, REMOTE_ADDR=IP) == 429


@PLAIN
@pytest.mark.usefixtures("three_a_minute")
def test_a_forbidden_key_is_not_a_refused_key(client: Client) -> None:
    """403 (scope, IP allow-list) answers a valid key: it is not a guess and does not count."""
    _, key = ApiClientFactory.create_with_key(scopes=[])
    for _ in range(5):
        assert client.get(PROTECTED, headers=_bearer(key), REMOTE_ADDR=IP).status_code == 403
    assert _refused(client, url=PROTECTED, REMOTE_ADDR=IP) == 401


@pytest.mark.usefixtures("three_a_minute")
def test_an_ipv6_client_is_counted_by_its_64_network(client: Client) -> None:
    for i in range(3):
        assert _refused(client, REMOTE_ADDR=f"2001:db8:9:1::{i + 1:x}") == 401
    assert _refused(client, REMOTE_ADDR="2001:db8:9:1::ffff") == 429
    assert _refused(client, REMOTE_ADDR="2001:db8:9:2::1") == 401


@pytest.mark.usefixtures("three_a_minute")
def test_an_ipv4_mapped_ipv6_address_counts_as_its_ipv4_address(client: Client) -> None:
    _flood(client, 3, REMOTE_ADDR=f"::ffff:{IP}")
    assert _refused(client, REMOTE_ADDR=IP) == 429


@override_settings(**BEHIND_PROXY)
@pytest.mark.usefixtures("three_a_minute")
def test_behind_the_proxy_each_client_is_counted(client: Client) -> None:
    for _ in range(3):
        assert _refused(client, REMOTE_ADDR=PROXY, HTTP_X_REAL_IP=IP) == 401
    assert _refused(client, REMOTE_ADDR=PROXY, HTTP_X_REAL_IP=IP) == 429
    assert _refused(client, REMOTE_ADDR=PROXY, HTTP_X_REAL_IP=OTHER_IP) == 401


@override_settings(**BEHIND_PROXY)
@pytest.mark.usefixtures("three_a_minute")
def test_a_client_outside_the_proxy_cannot_choose_its_bucket(client: Client) -> None:
    for i in range(3):
        assert _refused(client, REMOTE_ADDR=IP, HTTP_X_REAL_IP=f"192.0.2.{i + 1}") == 401
    assert _refused(client, REMOTE_ADDR=IP, HTTP_X_REAL_IP="192.0.2.99") == 429


def test_settings_override_the_limit_and_the_block(
    client: Client, settings: SettingsWrapper
) -> None:
    settings.API_REFUSED_KEY_RATE_LIMIT = "2/minute"
    settings.API_REFUSED_KEY_BLOCK_SECONDS = 60
    assert _flood(client, 3, REMOTE_ADDR=IP) == [401, 401, 429]
    response = client.get(ME, headers=_bearer(generate_key().key), REMOTE_ADDR=IP)
    assert 58 <= int(response["Retry-After"]) <= 60


# --- The rolling window, on the service with an explicit clock


@pytest.mark.usefixtures("three_a_minute")
def test_the_window_rolls_across_minute_boundaries() -> None:
    """Two refusals at 12:00:50 and one at 12:01:10 are three within a minute: a fixed window
    aligned on the minute would have forgotten the first two."""
    at = NOON + dt.timedelta(seconds=50)
    assert not record_refused_key(IP, now=at)
    assert not record_refused_key(IP, now=at)
    assert record_refused_key(IP, now=NOON + dt.timedelta(seconds=70))
    assert refused_key_retry_after(IP, now=NOON + dt.timedelta(seconds=71)) > 0


@pytest.mark.usefixtures("three_a_minute")
def test_refusals_older_than_the_window_are_forgotten() -> None:
    record_refused_key(IP, now=NOON)
    record_refused_key(IP, now=NOON + dt.timedelta(seconds=30))
    assert not record_refused_key(IP, now=NOON + dt.timedelta(seconds=61))
    assert refused_key_retry_after(IP, now=NOON + dt.timedelta(seconds=62)) == 0
    assert record_refused_key(IP, now=NOON + dt.timedelta(seconds=62))


@pytest.mark.usefixtures("three_a_minute")
def test_a_refusal_cleans_only_the_old_rows_of_its_own_ip() -> None:
    """Under a flood each 401 must not sweep the whole table (lane-b re-check finding 1)."""
    record_refused_key(IP, now=NOON)
    record_refused_key(OTHER_IP, now=NOON)
    with CaptureQueriesContext(connection) as queries:
        record_refused_key(IP, now=NOON + dt.timedelta(minutes=5))
    deletes = [q["sql"] for q in queries.captured_queries if q["sql"].startswith("DELETE")]
    assert deletes
    assert all('"ip_bucket" =' in sql and IP in sql for sql in deletes)
    assert set(ApiRefusedKey.objects.values_list("ip_bucket", flat=True)) == {IP, OTHER_IP}
    assert ApiRefusedKey.objects.filter(ip_bucket=IP).count() == 1


@pytest.mark.usefixtures("three_a_minute")
def test_the_nightly_purge_deletes_the_old_refusals_of_every_ip() -> None:
    record_refused_key(IP, now=NOON)
    record_refused_key(OTHER_IP, now=NOON)
    record_refused_key(IP, now=NOON + dt.timedelta(minutes=5))
    deleted = purge_expired_refused_keys(now=NOON + dt.timedelta(minutes=5))
    assert deleted == 1  # the IP cleaned its own old row; OTHER_IP waited for the purge
    assert list(ApiRefusedKey.objects.values_list("ip_bucket", flat=True)) == [IP]


def test_the_refused_key_purge_runs_every_night() -> None:
    purge = {s.task_name: s for s in schedules()}["api.purge_refused_keys"]
    assert purge.cron == "22 4 * * *"
    ApiRefusedKeyFactory.create(ip_bucket=IP, refused_at=timezone.now() - dt.timedelta(days=1))
    api_tasks.purge_refused_keys.func(timestamp=0)
    assert not ApiRefusedKey.objects.exists()


@pytest.mark.usefixtures("three_a_minute")
def test_a_block_is_started_and_logged_once(caplog: pytest.LogCaptureFixture) -> None:
    """Two 401s that both count past the limit (in flight together) start one block.

    The second one sees the refusals it raced with: they are re-inserted here, as they would be
    when both requests insert before either clears the bucket (lane-b re-check finding 2).
    """
    with caplog.at_level(logging.WARNING, logger="apps.api.services"):
        record_refused_key(IP, now=NOON)
        record_refused_key(IP, now=NOON)
        assert record_refused_key(IP, now=NOON)
        ApiRefusedKey.objects.bulk_create(
            [ApiRefusedKey(ip_bucket=IP, refused_at=NOON) for _ in range(2)]
        )
        assert not record_refused_key(IP, now=NOON + dt.timedelta(seconds=1))
    events = [r for r in caplog.records if "refused-key limit" in r.getMessage()]
    assert len(events) == 1
    block = Throttle.objects.get(key__startswith="api:refused-key:")
    assert block.locked_until == NOON + dt.timedelta(seconds=300)


def test_the_block_lasts_the_configured_time(settings: SettingsWrapper) -> None:
    settings.API_REFUSED_KEY_RATE_LIMIT = "1/minute"
    settings.API_REFUSED_KEY_BLOCK_SECONDS = 300
    assert record_refused_key(IP, now=NOON)
    assert refused_key_retry_after(IP, now=NOON + dt.timedelta(seconds=299)) == 1
    assert refused_key_retry_after(IP, now=NOON + dt.timedelta(seconds=300)) == 0


def test_a_request_without_an_ip_is_not_counted(settings: SettingsWrapper) -> None:
    settings.API_REFUSED_KEY_RATE_LIMIT = "1/minute"
    assert not record_refused_key(None, now=NOON)
    assert refused_key_retry_after(None, now=NOON) == 0


@pytest.mark.usefixtures("three_a_minute")
def test_the_block_is_logged_without_key_material(
    client: Client, caplog: pytest.LogCaptureFixture
) -> None:
    generated = [generate_key() for _ in range(3)]
    with caplog.at_level(logging.WARNING, logger="apps.api.services"):
        for key in (g.key for g in generated):
            client.get(ME, headers=_bearer(key), REMOTE_ADDR=IP)
    events = [r for r in caplog.records if "refused-key limit" in r.getMessage()]
    assert len(events) == 1
    assert IP in events[0].getMessage()
    # The secret part of a urlsafe key can itself contain "_", so split it off by position,
    # never by separator: a one-character fragment would match any log line.
    for g in generated:
        secret = g.key.removeprefix(f"{KEY_NAMESPACE}_{g.prefix}_")
        assert len(secret) >= 32
        assert len(g.prefix) >= 8
        for material in (g.key, secret, g.prefix):
            assert material not in caplog.text
