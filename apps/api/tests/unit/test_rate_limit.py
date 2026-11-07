"""CORE.12: the per-API-key rate limit, pure part (rates, buckets, windows, decisions)."""

import datetime as dt
import uuid

import pytest
from pytest_django.fixtures import SettingsWrapper

from apps.api.checks import check_rate_limit_settings
from apps.api.engine import (
    KEY_WIDE_BUCKET,
    InvalidRateLimit,
    Rate,
    limit_for,
    parse_rate,
    rate_decision,
    rate_policy,
    window_start,
)

UTC = dt.UTC
CLIENT = str(uuid.UUID(int=1))
OTHER_CLIENT = str(uuid.UUID(int=2))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("600/minute", Rate(limit=600, period=60)),
        ("10/s", Rate(limit=10, period=1)),
        ("10/second", Rate(limit=10, period=1)),
        ("30/min", Rate(limit=30, period=60)),
        ("5000/hour", Rate(limit=5000, period=3600)),
        ("2/h", Rate(limit=2, period=3600)),
        (" 100000 / day ", Rate(limit=100000, period=86400)),
    ],
)
def test_parse_rate_reads_count_and_period(text: str, expected: Rate) -> None:
    assert parse_rate(text) == expected


@pytest.mark.parametrize(
    "text", ["", "600", "0/minute", "-1/minute", "ten/minute", "10/fortnight", "10/minute/2"]
)
def test_parse_rate_refuses_anything_else(text: str) -> None:
    with pytest.raises(InvalidRateLimit):
        parse_rate(text)


def test_window_start_is_aligned_on_the_period() -> None:
    now = dt.datetime(2026, 11, 7, 12, 34, 56, 700000, tzinfo=UTC)
    assert window_start(now, Rate(limit=1, period=60)) == dt.datetime(
        2026, 11, 7, 12, 34, tzinfo=UTC
    )
    assert window_start(now, Rate(limit=1, period=3600)) == dt.datetime(2026, 11, 7, 12, tzinfo=UTC)
    assert window_start(now, Rate(limit=1, period=86400)) == dt.datetime(2026, 11, 7, tzinfo=UTC)


def test_window_start_is_the_same_instant_whatever_the_time_zone() -> None:
    rome = dt.timezone(dt.timedelta(hours=1))
    now = dt.datetime(2026, 11, 7, 13, 34, 56, tzinfo=rome)
    assert window_start(now, Rate(limit=1, period=60)) == dt.datetime(
        2026, 11, 7, 12, 34, tzinfo=UTC
    )


def test_calls_up_to_the_limit_are_allowed_with_what_remains() -> None:
    rate = Rate(limit=3, period=60)
    start = dt.datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
    now = start + dt.timedelta(seconds=10)
    first = rate_decision(hits=1, window_start=start, rate=rate, now=now)
    last = rate_decision(hits=3, window_start=start, rate=rate, now=now)
    assert (first.allowed, first.limit, first.remaining, first.retry_after) == (True, 3, 2, 0)
    assert (last.allowed, last.remaining, last.retry_after) == (True, 0, 0)


def test_the_call_over_the_limit_waits_until_the_window_ends() -> None:
    rate = Rate(limit=3, period=60)
    start = dt.datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
    over = rate_decision(
        hits=4, window_start=start, rate=rate, now=start + dt.timedelta(seconds=10, microseconds=1)
    )
    assert (over.allowed, over.remaining) == (False, 0)
    assert over.retry_after == 50  # rounded up: never invite a retry before the window ends


def test_retry_after_is_at_least_one_second() -> None:
    rate = Rate(limit=1, period=60)
    start = dt.datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
    almost_over = start + dt.timedelta(seconds=59, microseconds=999999)
    assert rate_decision(hits=2, window_start=start, rate=rate, now=almost_over).retry_after == 1


def test_a_scope_without_its_own_limit_uses_the_key_wide_counter() -> None:
    policy = rate_policy("600/minute", {"catalog:write": "60/minute"}, {})
    assert limit_for(policy, scope="catalog:read", client_id=CLIENT) == (
        KEY_WIDE_BUCKET,
        Rate(limit=600, period=60),
    )
    assert limit_for(policy, scope=None, client_id=CLIENT) == (
        KEY_WIDE_BUCKET,
        Rate(limit=600, period=60),
    )


def test_a_scope_with_its_own_limit_has_its_own_counter() -> None:
    policy = rate_policy("600/minute", {"catalog:write": "60/minute"}, {})
    assert limit_for(policy, scope="catalog:write", client_id=CLIENT) == (
        "catalog:write",
        Rate(limit=60, period=60),
    )


def test_a_client_override_replaces_the_key_wide_limit_of_that_key_only() -> None:
    policy = rate_policy("600/minute", {"catalog:write": "60/minute"}, {CLIENT: "5000/hour"})
    assert limit_for(policy, scope="catalog:read", client_id=CLIENT) == (
        KEY_WIDE_BUCKET,
        Rate(limit=5000, period=3600),
    )
    assert limit_for(policy, scope="catalog:read", client_id=OTHER_CLIENT)[1] == Rate(600, 60)
    # Scope limits protect costly operations from every key, overridden ones included.
    assert limit_for(policy, scope="catalog:write", client_id=CLIENT)[1] == Rate(60, 60)


@pytest.mark.parametrize(
    ("scopes", "clients"),
    [
        ({"catalog": "1/minute"}, {}),  # not resource:action
        ({"catalog:write": "often"}, {}),
        ({}, {"not-a-uuid": "1/minute"}),
        ({}, {CLIENT: "0/minute"}),
    ],
)
def test_rate_policy_refuses_invalid_entries(
    scopes: dict[str, str], clients: dict[str, str]
) -> None:
    with pytest.raises(InvalidRateLimit):
        rate_policy("600/minute", scopes, clients)


def test_the_system_check_accepts_the_shipped_settings() -> None:
    assert check_rate_limit_settings() == []


def test_the_system_check_reports_an_invalid_rate_limit_setting(settings: SettingsWrapper) -> None:
    settings.API_RATE_LIMIT_SCOPES = {"catalog:write": "lots"}
    errors = check_rate_limit_settings()
    assert [e.id for e in errors] == ["api.E003"]
    assert "API_RATE_LIMIT" in errors[0].msg
