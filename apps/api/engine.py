"""API keys: format, hashing and the authorization decision (CORE.6). Pure functions, no ORM.

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
import re
import secrets
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

KEY_NAMESPACE: Final = "mpmv"
PREFIX_LENGTH: Final = 8
SECRET_BYTES: Final = 32

_KEY_RE: Final = re.compile(
    rf"^{KEY_NAMESPACE}_(?P<prefix>[0-9a-f]{{{PREFIX_LENGTH}}})_(?P<secret>[A-Za-z0-9_-]{{32,}})$"
)
_SCOPE_RE: Final = re.compile(r"^[a-z][a-z0-9_-]*:[a-z][a-z0-9_-]*$")


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


@dataclass(frozen=True)
class Decision:
    status: AuthStatus
    reason: Reason | None

    @property
    def http_status(self) -> int:
        return self.status.http_status


ALLOWED: Final = Decision(AuthStatus.OK, None)


def deny(reason: Reason) -> Decision:
    forbidden = reason in (Reason.IP_NOT_ALLOWED, Reason.INSUFFICIENT_SCOPE)
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


def evaluate(
    state: ClientState, *, now: dt.datetime, ip: str | None, required_scope: str
) -> Decision:
    """Order: revoked → expired (401), then IP allow-list → scope (403)."""
    if not state.is_active:
        return deny(Reason.REVOKED)
    if state.expires_at <= now:
        return deny(Reason.EXPIRED)
    if not ip_allowed(ip, state.allowed_ips):
        return deny(Reason.IP_NOT_ALLOWED)
    if required_scope not in state.scopes:
        return deny(Reason.INSUFFICIENT_SCOPE)
    return ALLOWED


# --- Issuance input


def validate_scopes(scopes: Iterable[str]) -> list[str]:
    """Scopes look like `resource:action` (e.g. `catalog:write`); matched exactly, no wildcard."""
    cleaned = sorted(set(scopes))
    if not cleaned:
        raise InvalidApiClient("an API client needs at least one scope")
    invalid = [scope for scope in cleaned if not _SCOPE_RE.match(scope)]
    if invalid:
        raise InvalidApiClient(f"invalid scopes: {invalid!r} (expected resource:action)")
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
