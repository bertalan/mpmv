"""CORE.5: progressive backoff after repeated failures (pure engine, no database)."""

import datetime as dt

import pytest

from apps.core.throttling import (
    BackoffPolicy,
    ThrottleState,
    after_failure,
    ip_bucket,
    lock_duration,
)

T0 = dt.datetime(2027, 3, 1, 12, 0, tzinfo=dt.UTC)
MINUTE = dt.timedelta(minutes=1)
POLICY = BackoffPolicy(
    free_attempts=3,
    first_lock=MINUTE,
    max_lock=dt.timedelta(minutes=10),
    forget_after=dt.timedelta(hours=24),
)


def _fail(times: int, *, start: dt.datetime = T0, policy: BackoffPolicy = POLICY) -> ThrottleState:
    state = ThrottleState()
    for i in range(times):
        state = after_failure(state, start + i * dt.timedelta(seconds=1), policy)
    return state


@pytest.mark.parametrize(
    ("failures", "expected"),
    [
        (0, dt.timedelta(0)),
        (2, dt.timedelta(0)),
        (3, MINUTE),
        (4, 2 * MINUTE),
        (5, 4 * MINUTE),
        (6, 8 * MINUTE),
        (7, 10 * MINUTE),  # capped
        (500, 10 * MINUTE),  # no overflow on huge counts
    ],
)
def test_lock_starts_after_the_free_attempts_and_doubles_up_to_the_cap(
    failures: int, expected: dt.timedelta
) -> None:
    assert lock_duration(failures, POLICY) == expected


def test_failures_below_the_threshold_do_not_lock() -> None:
    state = _fail(2)
    assert state.failures == 2
    assert state.locked_until is None
    assert not state.is_locked(T0 + dt.timedelta(seconds=2))
    assert state.retry_after(T0) == dt.timedelta(0)


def test_the_nth_failure_locks_until_the_backoff_expires() -> None:
    state = _fail(3)
    last = T0 + dt.timedelta(seconds=2)
    assert state.failures == 3
    assert state.locked_until == last + MINUTE
    assert state.is_locked(last + MINUTE - dt.timedelta(microseconds=1))
    assert not state.is_locked(last + MINUTE)
    assert state.retry_after(last) == MINUTE


def test_each_failure_after_an_expired_lock_locks_for_longer() -> None:
    state = _fail(3)
    after_lock = state.locked_until
    assert after_lock is not None
    state = after_failure(state, after_lock, POLICY)
    assert state.failures == 4
    assert state.locked_until == after_lock + 2 * MINUTE


def test_a_quiet_period_forgets_old_failures() -> None:
    state = _fail(4)
    assert state.expires_at is not None
    later = state.expires_at + dt.timedelta(seconds=1)
    assert state.is_expired(later)
    state = after_failure(state, later, POLICY)
    assert state.failures == 1
    assert state.locked_until is None


def test_the_row_outlives_the_lock_and_the_quiet_period() -> None:
    long_lock = BackoffPolicy(
        free_attempts=1,
        first_lock=dt.timedelta(hours=48),
        max_lock=dt.timedelta(hours=48),
        forget_after=dt.timedelta(hours=1),
    )
    state = after_failure(ThrottleState(), T0, long_lock)
    assert state.expires_at == T0 + dt.timedelta(hours=48)
    single = after_failure(ThrottleState(), T0, POLICY)
    assert single.expires_at == T0 + dt.timedelta(hours=24)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"free_attempts": 0},
        {"first_lock": dt.timedelta(0)},
        {"max_lock": dt.timedelta(seconds=30)},  # shorter than the first lock
        {"forget_after": dt.timedelta(0)},
    ],
)
def test_policies_reject_values_that_would_disable_the_protection(
    kwargs: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "free_attempts": 3,
        "first_lock": MINUTE,
        "max_lock": 10 * MINUTE,
        "forget_after": dt.timedelta(hours=1),
        **kwargs,
    }
    with pytest.raises(ValueError, match="BackoffPolicy"):
        BackoffPolicy(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("ip", "expected"),
    [
        ("203.0.113.7", "203.0.113.7"),
        (" 203.0.113.7 ", "203.0.113.7"),
        ("2001:db8:1:2:aaaa::1", "2001:db8:1:2::/64"),
        ("2001:db8:1:2:ffff:ffff:ffff:ffff", "2001:db8:1:2::/64"),
        ("::ffff:203.0.113.7", "203.0.113.7"),  # IPv4-mapped IPv6 is the IPv4 client
        ("", None),
        (None, None),
        ("not-an-ip", "not-an-ip"),
    ],
)
def test_ip_bucket_groups_an_ipv6_client_by_its_64_network(
    ip: str | None, expected: str | None
) -> None:
    assert ip_bucket(ip) == expected
