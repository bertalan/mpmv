"""Progressive backoff after repeated failures (CORE.5). Pure functions, no ORM.

A throttle is a counter of failures for one key ("login:account:<email>", "login:ip:<ip>",
"register:ip:<ip>"…). The first `free_attempts - 1` failures cost nothing; the next one locks the
key for `first_lock`, and every failure after that doubles the lock, up to `max_lock`. A key
with no failure for `forget_after` (and no running lock) starts again from zero.

Storage lives in `apps.core.services` (`Throttle` rows in PostgreSQL).
"""

import datetime as dt
import ipaddress
from dataclasses import dataclass

_ZERO = dt.timedelta(0)
_MAX_DOUBLINGS = 32  # 2**32 times any sensible first lock is far beyond any cap


@dataclass(frozen=True)
class BackoffPolicy:
    free_attempts: int  # the failure that reaches this count starts the first lock
    first_lock: dt.timedelta
    max_lock: dt.timedelta
    forget_after: dt.timedelta  # a quiet period that resets the counter

    def __post_init__(self) -> None:
        if self.free_attempts < 1:
            raise ValueError("BackoffPolicy.free_attempts must be at least 1")
        if self.first_lock <= _ZERO or self.forget_after <= _ZERO:
            raise ValueError("BackoffPolicy durations must be positive")
        if self.max_lock < self.first_lock:
            raise ValueError("BackoffPolicy.max_lock must not be shorter than first_lock")


@dataclass(frozen=True)
class ThrottleState:
    failures: int = 0
    locked_until: dt.datetime | None = None
    expires_at: dt.datetime | None = None  # after this instant the state is forgotten

    def is_expired(self, now: dt.datetime) -> bool:
        return self.expires_at is not None and now >= self.expires_at

    def is_locked(self, now: dt.datetime) -> bool:
        return self.locked_until is not None and now < self.locked_until

    def retry_after(self, now: dt.datetime) -> dt.timedelta:
        """How long until the lock ends; zero when the key is not locked."""
        if self.locked_until is None or not self.is_locked(now):
            return _ZERO
        return self.locked_until - now


def lock_duration(failures: int, policy: BackoffPolicy) -> dt.timedelta:
    """The lock that starts when the counter reaches `failures`."""
    over = failures - policy.free_attempts
    if over < 0:
        return _ZERO
    factor: int = 2 ** min(over, _MAX_DOUBLINGS)
    return min(policy.first_lock * factor, policy.max_lock)


def after_failure(state: ThrottleState, now: dt.datetime, policy: BackoffPolicy) -> ThrottleState:
    """The state after one more failure at `now`."""
    failures = 1 if state.is_expired(now) else state.failures + 1
    lock = lock_duration(failures, policy)
    locked_until = now + lock if lock else None
    expires_at = max(now + policy.forget_after, locked_until or now)
    return ThrottleState(failures=failures, locked_until=locked_until, expires_at=expires_at)


def ip_bucket(ip: str | None) -> str | None:
    """The unit an IP is throttled by: the IPv4 address, or the /64 network of an IPv6 one.

    One IPv6 subscriber usually owns a whole /64, so throttling single addresses would be
    trivial to evade. Unparseable input is kept as is (it still names a client).
    """
    if ip is None or not ip.strip():
        return None
    raw = ip.strip()
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        return raw[:64]
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return str(address.ipv4_mapped)
        return str(ipaddress.IPv6Network((address, 64), strict=False))
    return str(address)
