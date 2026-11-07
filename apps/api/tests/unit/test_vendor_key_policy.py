"""CORE.14: the vendor API key policy, pure part (no database).

- A vendor key holds only vendor-grantable scopes (`VENDOR_GRANTABLE_SCOPES`); every other scope
  is platform-only.
- A key of a suspended vendor may only read: writes are refused with 403.
- A key of a closed vendor is refused with 401, reads included.
"""

import datetime as dt

import pytest

from apps.api.engine import (
    MUTATING_METHODS,
    VENDOR_GRANTABLE_SCOPES,
    AuthStatus,
    ClientState,
    InvalidApiClient,
    Reason,
    bearer_challenge,
    deny,
    evaluate,
    is_write,
    validate_scopes,
)
from apps.api.problems import auth_problem
from apps.vendors import engine as vendor_engine

NOW = dt.datetime(2026, 11, 7, 12, 0, tzinfo=dt.UTC)
SCOPE_FORMAT_EXAMPLE = "catalog:write"
PLATFORM_ONLY = "clients:write"


def _state(vendor_status: str | None) -> ClientState:
    return ClientState(
        is_active=True,
        expires_at=NOW + dt.timedelta(days=30),
        scopes=("catalog:read", "catalog:write"),
        allowed_ips=None,
        vendor_status=vendor_status,
    )


def _decide(vendor_status: str | None, *, write: bool, scope: str | None = "catalog:write") -> str:
    state = _state(vendor_status)
    decision = evaluate(state, now=NOW, ip=None, required_scope=scope, write=write)
    return decision.status


# --- Scopes


def test_vendor_grantable_scopes_are_well_formed_scopes() -> None:
    assert VENDOR_GRANTABLE_SCOPES
    assert validate_scopes(VENDOR_GRANTABLE_SCOPES) == sorted(VENDOR_GRANTABLE_SCOPES)


def test_vendor_grantable_scopes_cover_the_vendor_pipelines_and_events() -> None:
    expected = {"catalog:read", "catalog:write", "stock:read", "stock:write"}
    expected |= {"events:read", "events:write"}  # EVT.5
    assert expected <= VENDOR_GRANTABLE_SCOPES


def test_api_client_management_is_platform_only() -> None:
    assert PLATFORM_ONLY not in VENDOR_GRANTABLE_SCOPES


def test_a_vendor_key_takes_vendor_grantable_scopes() -> None:
    scopes = ["events:read", SCOPE_FORMAT_EXAMPLE]
    assert validate_scopes(scopes, vendor_key=True) == sorted(scopes)


@pytest.mark.parametrize(
    "scopes",
    [[PLATFORM_ONLY], [SCOPE_FORMAT_EXAMPLE, PLATFORM_ONLY], ["reports:read"]],
)
def test_a_vendor_key_refuses_platform_only_scopes(scopes: list[str]) -> None:
    with pytest.raises(InvalidApiClient, match="platform-only"):
        validate_scopes(scopes, vendor_key=True)


def test_the_refusal_names_the_platform_only_scopes() -> None:
    with pytest.raises(InvalidApiClient, match=PLATFORM_ONLY):
        validate_scopes([SCOPE_FORMAT_EXAMPLE, PLATFORM_ONLY], vendor_key=True)


def test_a_platform_key_takes_any_well_formed_scope() -> None:
    assert validate_scopes([PLATFORM_ONLY, SCOPE_FORMAT_EXAMPLE]) == [
        SCOPE_FORMAT_EXAMPLE,
        PLATFORM_ONLY,
    ]


# --- Reads and writes


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "post"])
def test_mutating_methods_are_writes(method: str) -> None:
    assert is_write(method)


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS", "get"])
def test_safe_methods_are_reads(method: str) -> None:
    assert not is_write(method)


def test_mutating_methods_are_the_four_unsafe_ones() -> None:
    assert frozenset({"POST", "PUT", "PATCH", "DELETE"}) == MUTATING_METHODS


# --- Vendor status


@pytest.mark.parametrize(
    "status",
    [None, vendor_engine.APPLICANT, vendor_engine.ONBOARDING, vendor_engine.ACTIVE],
)
@pytest.mark.parametrize("write", [False, True])
def test_platform_keys_and_keys_of_open_vendors_read_and_write(
    status: str | None, write: bool
) -> None:
    assert _decide(status, write=write) is AuthStatus.OK


def test_a_suspended_vendors_key_reads() -> None:
    assert _decide(vendor_engine.SUSPENDED, write=False, scope="catalog:read") is AuthStatus.OK


def test_a_suspended_vendors_key_reads_operations_without_a_scope() -> None:
    assert _decide(vendor_engine.SUSPENDED, write=False, scope=None) is AuthStatus.OK


def test_a_suspended_vendors_key_cannot_write() -> None:
    decision = evaluate(
        _state(vendor_engine.SUSPENDED),
        now=NOW,
        ip=None,
        required_scope="catalog:write",
        write=True,
    )
    assert decision == deny(Reason.VENDOR_SUSPENDED)
    assert decision.http_status == 403


@pytest.mark.parametrize("write", [False, True])
def test_a_closed_vendors_key_is_unauthenticated(write: bool) -> None:
    decision = evaluate(
        _state(vendor_engine.CLOSED), now=NOW, ip=None, required_scope="catalog:read", write=write
    )
    assert decision == deny(Reason.VENDOR_CLOSED)
    assert decision.http_status == 401


def test_revocation_is_reported_before_the_vendor_closure() -> None:
    state = ClientState(
        is_active=False,
        expires_at=NOW + dt.timedelta(days=30),
        scopes=("catalog:read",),
        allowed_ips=None,
        vendor_status=vendor_engine.CLOSED,
    )
    decision = evaluate(state, now=NOW, ip=None, required_scope=None)
    assert decision.reason is Reason.REVOKED


def test_the_vendor_status_defaults_to_a_platform_key() -> None:
    state = ClientState(
        is_active=True, expires_at=NOW + dt.timedelta(days=1), scopes=(), allowed_ips=None
    )
    assert state.vendor_status is None


# --- Problems and challenges


def test_a_closed_vendors_key_gets_an_invalid_token_challenge() -> None:
    challenge = bearer_challenge(deny(Reason.VENDOR_CLOSED), "catalog:read", realm="api")
    assert challenge == 'Bearer realm="api", error="invalid_token"'


def test_a_suspended_vendors_write_gets_no_challenge() -> None:
    assert bearer_challenge(deny(Reason.VENDOR_SUSPENDED), "catalog:write", realm="api") is None


@pytest.mark.parametrize(
    ("reason", "status"), [(Reason.VENDOR_CLOSED, 401), (Reason.VENDOR_SUSPENDED, 403)]
)
def test_vendor_refusals_are_problems_with_their_own_code(reason: Reason, status: int) -> None:
    body = auth_problem(deny(reason), "catalog:write").to_dict()
    assert body["status"] == status
    assert body["code"] == str(reason)
    assert body["detail"]
