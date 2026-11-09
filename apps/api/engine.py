"""API keys: format, hashing, the authorization decision (CORE.6) and the per-key rate limit
(CORE.12). Pure functions, no ORM.

Key format: `mpmv_<prefix>_<secret>`.

- `prefix`: 8 lowercase hex characters, public, unique; used to look the client up.
- `secret`: 32 random bytes, URL-safe base64 (256 bits of entropy).

Only the SHA-256 of the full key is stored. A slow password hash is not needed: the key is
random and high-entropy, so it cannot be brute-forced from its hash. Hashes are compared in
constant time.
"""

import datetime as dt
import hashlib
import hmac
import ipaddress
import math
import re
import secrets
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from apps.vendors import engine as vendor_engine

KEY_NAMESPACE: Final = "mpmv"
PREFIX_LENGTH: Final = 8
SECRET_BYTES: Final = 32

_KEY_RE: Final = re.compile(
    rf"^{KEY_NAMESPACE}_(?P<prefix>[0-9a-f]{{{PREFIX_LENGTH}}})_(?P<secret>[A-Za-z0-9_-]{{32,}})$"
)
_SCOPE_RE: Final = re.compile(r"^[a-z][a-z0-9_-]*:[a-z][a-z0-9_-]*$")

# The scopes a vendor key may hold (CORE.14): operations on the vendor's own data only, scoped by
# `for_actor` (CORE.3). Every other scope is platform-only. An allow-list, so a new scope stays
# platform-only until it is added here on purpose; vendor-facing endpoints (EVT.5 and later)
# add their scopes to this set, and only here.
VENDOR_GRANTABLE_SCOPES: Final = frozenset(
    {
        "catalog:read",
        "catalog:write",
        "stock:read",
        "stock:write",
        "events:read",  # EVT.5
        "events:write",  # EVT.5
    }
)

# HTTP methods that change state. Only a key of an active vendor may use them (CORE.14), dry-runs
# included; API v1 also requires `?dry_run=` on each of them (CORE.7).
MUTATING_METHODS: Final = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def is_write(method: str) -> bool:
    return method.upper() in MUTATING_METHODS


def platform_only_scopes(scopes: Iterable[str]) -> list[str]:
    """The scopes in `scopes` that a vendor key may not hold (outside `VENDOR_GRANTABLE_SCOPES`)."""
    return sorted({scope for scope in scopes if scope not in VENDOR_GRANTABLE_SCOPES})


class InvalidApiClient(ValueError):
    """Raised for invalid issuance or rotation input (scopes, expiry, allow-list, state)."""


class AuthStatus(StrEnum):
    OK = "ok"
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"

    @property
    def http_status(self) -> int:
        return {"ok": 200, "unauthenticated": 401, "forbidden": 403}[self.value]


class Reason(StrEnum):
    """Why a request was refused. Unknown prefix and wrong secret share INVALID (no enumeration)."""

    MISSING = "missing_credentials"
    MALFORMED = "malformed_credentials"
    INVALID = "invalid_key"
    REVOKED = "revoked"
    EXPIRED = "expired"
    IP_NOT_ALLOWED = "ip_not_allowed"
    INSUFFICIENT_SCOPE = "insufficient_scope"
    VENDOR_CLOSED = "vendor_closed"  # CORE.14: the key's vendor is closed
    VENDOR_SUSPENDED = "vendor_suspended"  # CORE.14: a write by a suspended vendor's key
    VENDOR_NOT_ACTIVE = "vendor_not_active"  # CORE.14: a write by a not yet active vendor's key


_FORBIDDEN_REASONS: Final = frozenset(
    {
        Reason.IP_NOT_ALLOWED,
        Reason.INSUFFICIENT_SCOPE,
        Reason.VENDOR_SUSPENDED,
        Reason.VENDOR_NOT_ACTIVE,
    }
)


@dataclass(frozen=True)
class Decision:
    status: AuthStatus
    reason: Reason | None

    @property
    def http_status(self) -> int:
        return self.status.http_status


ALLOWED: Final = Decision(AuthStatus.OK, None)


def deny(reason: Reason) -> Decision:
    forbidden = reason in _FORBIDDEN_REASONS
    return Decision(AuthStatus.FORBIDDEN if forbidden else AuthStatus.UNAUTHENTICATED, reason)


# --- Keys


@dataclass(frozen=True)
class GeneratedKey:
    prefix: str
    key: str  # the full key; shown once at issuance, never stored


@dataclass(frozen=True)
class ParsedKey:
    prefix: str
    key: str


def generate_key() -> GeneratedKey:
    prefix = secrets.token_hex(PREFIX_LENGTH // 2)
    secret = secrets.token_urlsafe(SECRET_BYTES)
    return GeneratedKey(prefix=prefix, key=f"{KEY_NAMESPACE}_{prefix}_{secret}")


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def key_matches(key: str, stored_hash: str) -> bool:
    """Constant-time comparison of the key's hash with the stored one."""
    return hmac.compare_digest(hash_key(key), stored_hash)


def parse_authorization(header: str | None) -> ParsedKey | Decision:
    """Parse `Authorization: Bearer mpmv_<prefix>_<secret>`; a Decision means refused."""
    if header is None or not header.strip():
        return deny(Reason.MISSING)
    parts = header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return deny(Reason.MALFORMED)
    match = _KEY_RE.match(parts[1])
    if match is None:
        return deny(Reason.MALFORMED)
    return ParsedKey(prefix=match["prefix"], key=parts[1])


# --- Authorization decision


@dataclass(frozen=True)
class ClientState:
    """What the decision needs from a stored client whose key already matched."""

    is_active: bool
    expires_at: dt.datetime
    scopes: Sequence[str]
    allowed_ips: Sequence[str] | None
    vendor_status: str | None = None  # the status of the key's vendor; None for a platform key


def ip_allowed(ip: str | None, allowed: Sequence[str] | None) -> bool:
    """`None` allows any address; otherwise the address must fall in one of the networks."""
    if allowed is None:
        return True
    if ip is None:
        return False
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(address in ipaddress.ip_network(network) for network in allowed)


def vendor_write_refusal(vendor_status: str) -> Reason | None:
    """Why a vendor key may not write while its vendor has `vendor_status`; None when it may.

    Only an active vendor's key writes (CORE.14): a closed vendor's key is refused outright (401),
    a suspended one's and a not yet active one's (applicant, onboarding) only read (403).
    """
    if vendor_status == vendor_engine.ACTIVE:
        return None
    if vendor_status == vendor_engine.CLOSED:
        return Reason.VENDOR_CLOSED
    if vendor_status == vendor_engine.SUSPENDED:
        return Reason.VENDOR_SUSPENDED
    return Reason.VENDOR_NOT_ACTIVE


def evaluate(
    state: ClientState,
    *,
    now: dt.datetime,
    ip: str | None,
    required_scope: str | None,
    write: bool = True,
) -> Decision:
    """Order: revoked → expired → closed vendor (401), then IP allow-list → scope → a write by a
    vendor that is not active (403).

    `required_scope=None` is for operations open to any valid key (e.g. `GET /api/v1/me`).
    A vendor key never passes the scope check for a platform-only scope, even if it holds it (a
    key issued before the scope was classed platform-only): the allow-list is enforced here too,
    not only at issuance. `write` says whether the call changes state (`is_write(method)`); it
    defaults to True so a caller that does not say is refused like a write (CORE.14: only an
    active vendor's key writes).
    """
    vendor_key = state.vendor_status is not None
    if not state.is_active:
        return deny(Reason.REVOKED)
    if state.expires_at <= now:
        return deny(Reason.EXPIRED)
    if state.vendor_status == vendor_engine.CLOSED:
        return deny(Reason.VENDOR_CLOSED)
    if not ip_allowed(ip, state.allowed_ips):
        return deny(Reason.IP_NOT_ALLOWED)
    if required_scope is not None and (
        required_scope not in state.scopes
        or (vendor_key and required_scope not in VENDOR_GRANTABLE_SCOPES)
    ):
        return deny(Reason.INSUFFICIENT_SCOPE)
    if write and state.vendor_status is not None:
        refusal = vendor_write_refusal(state.vendor_status)
        if refusal is not None:
            return deny(refusal)
    return ALLOWED


def bearer_challenge(decision: Decision, required_scope: str | None, *, realm: str) -> str | None:
    """The `WWW-Authenticate` value for a refusal (RFC 6750 §3), or None when none is due.

    401 always gets a challenge (`invalid_token` unless credentials were missing); 403 gets one
    only for a missing scope. A refused IP gets none: no token change would help.
    """
    if decision.status is AuthStatus.UNAUTHENTICATED:
        challenge = f'Bearer realm="{realm}"'
        if decision.reason is not Reason.MISSING:
            challenge += ', error="invalid_token"'
        return challenge
    if decision.reason is Reason.INSUFFICIENT_SCOPE and required_scope is not None:
        return f'Bearer realm="{realm}", error="insufficient_scope", scope="{required_scope}"'
    return None


# --- Issuance input


def validate_scopes(scopes: Iterable[str], *, vendor_key: bool = False) -> list[str]:
    """Scopes look like `resource:action` (e.g. `catalog:write`); matched exactly, no wildcard.

    A vendor key (`vendor_key=True`) takes only `VENDOR_GRANTABLE_SCOPES` (CORE.14).
    """
    cleaned = sorted(set(scopes))
    if not cleaned:
        raise InvalidApiClient("an API client needs at least one scope")
    invalid = [scope for scope in cleaned if not _SCOPE_RE.match(scope)]
    if invalid:
        raise InvalidApiClient(f"invalid scopes: {invalid!r} (expected resource:action)")
    if vendor_key:
        platform_only = platform_only_scopes(cleaned)
        if platform_only:
            raise InvalidApiClient(
                f"platform-only scopes cannot be granted to a vendor key: {platform_only!r}"
            )
    return cleaned


def normalize_allowed_ips(entries: Iterable[str] | None) -> list[str] | None:
    """Addresses or CIDR networks, stored as networks (`203.0.113.7` → `203.0.113.7/32`).

    `None` means any address. An empty list is refused: it would lock the client out.
    """
    if entries is None:
        return None
    networks: list[str] = []
    for entry in entries:
        try:
            networks.append(str(ipaddress.ip_network(entry.strip(), strict=False)))
        except ValueError as exc:
            raise InvalidApiClient(f"{entry!r} is not an IP address or network") from exc
    if not networks:
        raise InvalidApiClient("allowed_ips must be None (any address) or a non-empty list")
    return networks


# --- Rate limit (CORE.12)
#
# Fixed windows aligned on the epoch: a key may make `limit` calls per `period` seconds, counted
# from 12:00:00, 12:01:00… for a per-minute rate. Every call of an accepted key counts, those
# refused for the limit included, so hammering past the limit never shortens the wait. A key has one
# key-wide counter (`KEY_WIDE_BUCKET`), plus one per scope that has a limit of its own.
#
# A fixed window lets a client make up to twice its limit across a boundary; that is accepted
# for protecting the platform from runaway pipelines, which is what this limit is for.

KEY_WIDE_BUCKET: Final = "*"

_PERIODS: Final[Mapping[str, int]] = {
    "s": 1,
    "sec": 1,
    "second": 1,
    "m": 60,
    "min": 60,
    "minute": 60,
    "h": 3600,
    "hour": 3600,
    "d": 86400,
    "day": 86400,
}
_RATE_RE: Final = re.compile(r"^\s*(?P<limit>\d+)\s*/\s*(?P<period>[a-z]+)\s*$")


class InvalidRateLimit(ValueError):
    """Raised for a rate limit setting that cannot be read."""


@dataclass(frozen=True)
class Rate:
    limit: int  # calls allowed per window
    period: int  # window length in seconds


def parse_rate(text: str) -> Rate:
    """`"600/minute"` → `Rate(600, 60)`; units: s/sec/second, m/min/minute, h/hour, d/day."""
    match = _RATE_RE.match(text)
    if match is None or match["period"] not in _PERIODS:
        raise InvalidRateLimit(f"{text!r} is not a rate such as '600/minute'")
    limit = int(match["limit"])
    if limit < 1:
        raise InvalidRateLimit(f"{text!r}: the limit must be at least 1")
    return Rate(limit=limit, period=_PERIODS[match["period"]])


@dataclass(frozen=True)
class RatePolicy:
    default: Rate  # the key-wide limit of every key
    scopes: Mapping[str, Rate]  # scope → its own limit, with its own counter per key
    clients: Mapping[str, Rate]  # ApiClient public id → its key-wide limit instead of `default`


def rate_policy(default: str, scopes: Mapping[str, str], clients: Mapping[str, str]) -> RatePolicy:
    """Read the settings' rate limits; `InvalidRateLimit` names the first bad entry."""
    for scope in scopes:
        if not _SCOPE_RE.match(scope):
            raise InvalidRateLimit(f"{scope!r} is not a scope (expected resource:action)")
    parsed_clients: dict[str, Rate] = {}
    for client_id, text in clients.items():
        try:
            canonical = str(uuid.UUID(client_id))
        except ValueError as exc:
            raise InvalidRateLimit(f"{client_id!r} is not an API client public id") from exc
        parsed_clients[canonical] = parse_rate(text)
    return RatePolicy(
        default=parse_rate(default),
        scopes={scope: parse_rate(text) for scope, text in scopes.items()},
        clients=parsed_clients,
    )


def key_wide_rate(policy: RatePolicy, *, client_id: str, override: Rate | None = None) -> Rate:
    """The key-wide limit of one key. Precedence (CORE.16): the override staff stored on the
    `ApiClient`, then `policy.clients` (`API_RATE_LIMIT_CLIENTS`), then `policy.default`."""
    if override is not None:
        return override
    return policy.clients.get(client_id, policy.default)


def limits_for(
    policy: RatePolicy, *, scope: str | None, client_id: str, override: Rate | None = None
) -> tuple[tuple[str, Rate], ...]:
    """The counters a call of `client_id` to an operation needing `scope` counts on, with their
    rates; the call runs only if every one of them allows it.

    - A call to a scope without its own limit counts on the key-wide counter, at
      `key_wide_rate`.
    - A scope with its own limit counts on its own counter, for every key: it protects a costly
      operation, and a key's own limit never lifts it.
    - A key whose key-wide limit is below the default (`rate_is_below`) is capped on every
      call: its calls to a scope with its own limit count on the key-wide counter too (CORE.16;
      before, a capped key was not capped on such scopes).
    - Any other key (default limit, or one raised or equal to it) keeps the CORE.12 behaviour:
      those calls do not use up its key-wide budget, so a raise never cuts a key's unscoped
      budget below what a default key gets.
    """
    own = key_wide_rate(policy, client_id=client_id, override=override)
    key_wide = (KEY_WIDE_BUCKET, own)
    if scope is not None and scope in policy.scopes:
        scoped = (scope, policy.scopes[scope])
        return (scoped, key_wide) if rate_is_below(own, policy.default) else (scoped,)
    return (key_wide,)


def rate_is_below(rate: Rate, other: Rate) -> bool:
    """Whether `rate` allows fewer calls per second than `other` (exact, no floats)."""
    return rate.limit * other.period < other.limit * rate.period


def window_start(now: dt.datetime, rate: Rate) -> dt.datetime:
    """The start of the window `now` falls in, in UTC (windows are aligned on the epoch)."""
    seconds = math.floor(now.timestamp())
    return dt.datetime.fromtimestamp(seconds - seconds % rate.period, tz=dt.UTC)


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    limit: int
    remaining: int  # calls left in the window after this one
    retry_after: int  # whole seconds until the window ends when refused; 0 when allowed


def rate_decision(
    *, hits: int, window_start: dt.datetime, rate: Rate, now: dt.datetime
) -> RateDecision:
    """`hits` is the count in the current window, this call included."""
    if hits <= rate.limit:
        return RateDecision(
            allowed=True, limit=rate.limit, remaining=rate.limit - hits, retry_after=0
        )
    left = window_start + dt.timedelta(seconds=rate.period) - now
    retry_after = max(1, math.ceil(left.total_seconds()))
    return RateDecision(allowed=False, limit=rate.limit, remaining=0, retry_after=retry_after)


def combine_decisions(decisions: Sequence[RateDecision]) -> RateDecision:
    """One decision for a call counted on several counters: refused if any counter refuses (the
    one that frees up last sets `Retry-After`), otherwise the counter with the fewest calls left."""
    if not decisions:
        raise ValueError("a call counts on at least one counter")
    refused = [decision for decision in decisions if not decision.allowed]
    if refused:
        return max(refused, key=lambda decision: decision.retry_after)
    return min(decisions, key=lambda decision: decision.remaining)


# --- Refused-key limit per client IP (CORE.20)
#
# Refused keys (401) never reach the per-key counters. A client IP may get `rate.limit` refused
# answers in a rolling window of `rate.period` seconds; the answer that reaches the limit starts
# a block of `block` seconds, during which every call from that IP gets 429 before the key is
# read. Valid keys never count (they keep the CORE.12/CORE.16 per-key limits).


@dataclass(frozen=True)
class RefusedKeyPolicy:
    rate: Rate  # refused answers allowed per rolling window
    block: int  # seconds an IP stays blocked once it reaches the limit


def refused_key_policy(rate: str, block_seconds: object) -> RefusedKeyPolicy:
    """Read `API_REFUSED_KEY_RATE_LIMIT` and `API_REFUSED_KEY_BLOCK_SECONDS`."""
    if isinstance(block_seconds, bool) or not isinstance(block_seconds, int) or block_seconds < 1:
        raise InvalidRateLimit(f"{block_seconds!r}: the block must be whole seconds, at least 1")
    return RefusedKeyPolicy(rate=parse_rate(rate), block=block_seconds)


def seconds_left(until: dt.datetime, now: dt.datetime) -> int:
    """Whole seconds from `now` to `until`, rounded up; 0 once `until` has passed."""
    left = (until - now).total_seconds()
    return max(0, math.ceil(left))
