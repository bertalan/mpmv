"""CORE.6: API key format, hashing and the pure authorization decision (no database)."""

import datetime as dt
import hashlib
import re

import pytest

from apps.api.engine import (
    KEY_NAMESPACE,
    AuthStatus,
    ClientState,
    Decision,
    InvalidApiClient,
    ParsedKey,
    Reason,
    evaluate,
    generate_key,
    hash_key,
    key_matches,
    normalize_allowed_ips,
    parse_authorization,
    validate_scopes,
)

NOW = dt.datetime(2026, 11, 7, 12, 0, tzinfo=dt.UTC)
KEY_RE = re.compile(r"^mpmv_[0-9a-f]{8}_[A-Za-z0-9_-]{43,}$")


def _state(**overrides: object) -> ClientState:
    values: dict[str, object] = {
        "is_active": True,
        "expires_at": NOW + dt.timedelta(days=30),
        "scopes": ("catalog:write", "stock:write"),
        "allowed_ips": None,
    }
    values.update(overrides)
    return ClientState(**values)  # type: ignore[arg-type]


# --- Key generation and hashing


def test_generated_key_has_namespace_public_prefix_and_high_entropy_secret() -> None:
    generated = generate_key()
    assert KEY_NAMESPACE == "mpmv"
    assert KEY_RE.match(generated.key)
    assert generated.key.split("_", 2)[1] == generated.prefix
    assert len(generated.prefix) == 8


def test_generated_keys_are_unique() -> None:
    keys = {generate_key().key for _ in range(50)}
    assert len(keys) == 50


def test_hash_is_sha256_hex_of_the_full_key_and_never_contains_it() -> None:
    key = generate_key().key
    digest = hash_key(key)
    assert digest == hashlib.sha256(key.encode()).hexdigest()
    assert len(digest) == 64
    assert key not in digest


def test_key_matches_only_its_own_hash() -> None:
    key = generate_key().key
    other = generate_key().key
    stored = hash_key(key)
    assert key_matches(key, stored)
    assert not key_matches(other, stored)
    assert not key_matches(key[:-1], stored)


# --- Authorization header parsing


def test_parse_valid_bearer_header() -> None:
    generated = generate_key()
    parsed = parse_authorization(f"Bearer {generated.key}")
    assert parsed == ParsedKey(prefix=generated.prefix, key=generated.key)


def test_bearer_scheme_is_case_insensitive_and_surrounding_spaces_are_ignored() -> None:
    generated = generate_key()
    parsed = parse_authorization(f"  bearer   {generated.key} ")
    assert isinstance(parsed, ParsedKey)
    assert parsed.key == generated.key


@pytest.mark.parametrize("header", [None, "", "   "])
def test_missing_header_is_unauthenticated(header: str | None) -> None:
    assert parse_authorization(header) == Decision(AuthStatus.UNAUTHENTICATED, Reason.MISSING)


@pytest.mark.parametrize(
    "header",
    [
        "Basic " + "a" * 16,  # wrong scheme
        "Bearer",
        "Bearer not-a-key",
        "Bearer other_0123abcd_" + "x" * 43,  # wrong namespace
        "Bearer mpmv_0123abcd_",  # empty secret
        "Bearer mpmv_0123ABCD_" + "x" * 43,  # prefix must be lowercase hex
        "Bearer mpmv_0123abc_" + "x" * 43,  # prefix too short
        "Bearer mpmv_0123abcd_" + "x" * 43 + " extra",
    ],
)
def test_malformed_header_is_unauthenticated(header: str) -> None:
    assert parse_authorization(header) == Decision(AuthStatus.UNAUTHENTICATED, Reason.MALFORMED)


# --- Authorization decision


def test_active_unexpired_key_with_scope_is_allowed() -> None:
    decision = evaluate(_state(), now=NOW, ip="203.0.113.7", required_scope="catalog:write")
    assert decision == Decision(AuthStatus.OK, None)
    assert decision.http_status == 200


def test_revoked_key_is_unauthenticated() -> None:
    decision = evaluate(_state(is_active=False), now=NOW, ip=None, required_scope="catalog:write")
    assert decision == Decision(AuthStatus.UNAUTHENTICATED, Reason.REVOKED)
    assert decision.http_status == 401


@pytest.mark.parametrize("delta", [dt.timedelta(0), dt.timedelta(seconds=-1)])
def test_expired_key_is_unauthenticated(delta: dt.timedelta) -> None:
    decision = evaluate(
        _state(expires_at=NOW + delta), now=NOW, ip=None, required_scope="catalog:write"
    )
    assert decision == Decision(AuthStatus.UNAUTHENTICATED, Reason.EXPIRED)
    assert decision.http_status == 401


def test_revocation_wins_over_expiry() -> None:
    state = _state(is_active=False, expires_at=NOW - dt.timedelta(days=1))
    assert evaluate(state, now=NOW, ip=None, required_scope="x:y").reason == Reason.REVOKED


def test_key_without_the_required_scope_is_forbidden() -> None:
    decision = evaluate(_state(), now=NOW, ip=None, required_scope="tax:write")
    assert decision == Decision(AuthStatus.FORBIDDEN, Reason.INSUFFICIENT_SCOPE)
    assert decision.http_status == 403


def test_scopes_match_exactly_write_does_not_imply_read() -> None:
    decision = evaluate(_state(), now=NOW, ip=None, required_scope="catalog:read")
    assert decision.reason == Reason.INSUFFICIENT_SCOPE


@pytest.mark.parametrize(
    ("allowed", "ip", "status"),
    [
        (["203.0.113.7/32"], "203.0.113.7", AuthStatus.OK),
        (["198.51.100.0/24"], "198.51.100.42", AuthStatus.OK),
        (["2001:db8::/32"], "2001:db8::1", AuthStatus.OK),
        (["198.51.100.0/24"], "203.0.113.7", AuthStatus.FORBIDDEN),
        (["198.51.100.0/24"], None, AuthStatus.FORBIDDEN),
        (["198.51.100.0/24"], "garbage", AuthStatus.FORBIDDEN),
        (None, "203.0.113.7", AuthStatus.OK),
    ],
)
def test_ip_allow_list(allowed: list[str] | None, ip: str | None, status: AuthStatus) -> None:
    decision = evaluate(_state(allowed_ips=allowed), now=NOW, ip=ip, required_scope="catalog:write")
    assert decision.status == status
    if status == AuthStatus.FORBIDDEN:
        assert decision.reason == Reason.IP_NOT_ALLOWED


def test_ip_is_checked_before_scope() -> None:
    state = _state(allowed_ips=["198.51.100.0/24"])
    decision = evaluate(state, now=NOW, ip="203.0.113.7", required_scope="tax:write")
    assert decision.reason == Reason.IP_NOT_ALLOWED


# --- Input validation for issuance


def test_valid_scopes_are_deduplicated_and_sorted() -> None:
    assert validate_scopes(["stock:write", "catalog:write", "stock:write"]) == [
        "catalog:write",
        "stock:write",
    ]


@pytest.mark.parametrize(
    "scopes", [[], ["catalog"], ["Catalog:write"], ["catalog:"], [":write"], ["catalog:write "]]
)
def test_invalid_scopes_are_refused(scopes: list[str]) -> None:
    with pytest.raises(InvalidApiClient):
        validate_scopes(scopes)


def test_allowed_ips_are_normalized_to_networks() -> None:
    assert normalize_allowed_ips(None) is None
    assert normalize_allowed_ips(["203.0.113.7", "198.51.100.0/24", "2001:DB8::/32"]) == [
        "203.0.113.7/32",
        "198.51.100.0/24",
        "2001:db8::/32",
    ]


@pytest.mark.parametrize("entries", [[], ["not-an-ip"], ["198.51.100.0/33"]])
def test_invalid_allowed_ips_are_refused(entries: list[str]) -> None:
    with pytest.raises(InvalidApiClient):
        normalize_allowed_ips(entries)
