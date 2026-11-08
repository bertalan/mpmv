"""CORE.14: the vendor API key policy, pure part (no database).

- A vendor key holds only vendor-grantable scopes (`VENDOR_GRANTABLE_SCOPES`); every other scope
  is platform-only, and a vendor key is refused it at request time too, even if it holds it.
- Only a key of an active vendor writes: a key of an applicant, onboarding or suspended vendor
  may only read (403 on writes).
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
    platform_only_scopes,
    validate_scopes,
    vendor_write_refusal,
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


# --- Scopes at request time


def _holding(scopes: tuple[str, ...], vendor_status: str | None) -> ClientState:
    return ClientState(
        is_active=True,
        expires_at=NOW + dt.timedelta(days=30),
        scopes=scopes,
        allowed_ips=None,
        vendor_status=vendor_status,
    )


@pytest.mark.parametrize("write", [False, True])
def test_a_vendor_key_holding_a_platform_only_scope_is_refused_it(write: bool) -> None:
    # e.g. issued under CORE.13, before the scope was classed platform-only
    state = _holding((SCOPE_FORMAT_EXAMPLE, PLATFORM_ONLY), vendor_engine.ACTIVE)
    decision = evaluate(state, now=NOW, ip=None, required_scope=PLATFORM_ONLY, write=write)
    assert decision == deny(Reason.INSUFFICIENT_SCOPE)
    assert decision.http_status == 403


def test_the_same_vendor_key_keeps_its_vendor_grantable_scopes() -> None:
    state = _holding((SCOPE_FORMAT_EXAMPLE, PLATFORM_ONLY), vendor_engine.ACTIVE)
    decision = evaluate(state, now=NOW, ip=None, required_scope=SCOPE_FORMAT_EXAMPLE)
    assert decision.status is AuthStatus.OK


def test_a_vendor_key_with_a_platform_only_scope_still_calls_operations_without_a_scope() -> None:
    state = _holding((PLATFORM_ONLY,), vendor_engine.ACTIVE)
    assert evaluate(state, now=NOW, ip=None, required_scope=None, write=False).status is (
        AuthStatus.OK
    )


def test_a_platform_key_uses_its_platform_only_scope() -> None:
    state = _holding((PLATFORM_ONLY,), None)
    assert evaluate(state, now=NOW, ip=None, required_scope=PLATFORM_ONLY).status is AuthStatus.OK


def test_platform_only_scopes_are_those_outside_the_allow_list() -> None:
    scopes = [PLATFORM_ONLY, SCOPE_FORMAT_EXAMPLE, "reports:read", PLATFORM_ONLY]
    assert platform_only_scopes(scopes) == [PLATFORM_ONLY, "reports:read"]
    assert platform_only_scopes(VENDOR_GRANTABLE_SCOPES) == []


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


@pytest.mark.parametrize("status", [None, vendor_engine.ACTIVE])
@pytest.mark.parametrize("write", [False, True])
def test_platform_keys_and_keys_of_active_vendors_read_and_write(
    status: str | None, write: bool
) -> None:
    assert _decide(status, write=write) is AuthStatus.OK


@pytest.mark.parametrize("status", [vendor_engine.APPLICANT, vendor_engine.ONBOARDING])
def test_a_not_yet_active_vendors_key_reads(status: str) -> None:
    assert _decide(status, write=False, scope="catalog:read") is AuthStatus.OK


@pytest.mark.parametrize("status", [vendor_engine.APPLICANT, vendor_engine.ONBOARDING])
def test_a_not_yet_active_vendors_key_cannot_write(status: str) -> None:
    decision = evaluate(_state(status), now=NOW, ip=None, required_scope="catalog:write")
    assert decision == deny(Reason.VENDOR_NOT_ACTIVE)
    assert decision.http_status == 403


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (vendor_engine.ACTIVE, None),
        (vendor_engine.APPLICANT, Reason.VENDOR_NOT_ACTIVE),
        (vendor_engine.ONBOARDING, Reason.VENDOR_NOT_ACTIVE),
        (vendor_engine.SUSPENDED, Reason.VENDOR_SUSPENDED),
        (vendor_engine.CLOSED, Reason.VENDOR_CLOSED),
    ],
)
def test_only_an_active_vendor_writes(status: str, reason: Reason | None) -> None:
    assert vendor_write_refusal(status) == reason


def test_every_vendor_status_has_a_write_rule() -> None:
    writable = [s for s in vendor_engine.STATUSES if vendor_write_refusal(s) is None]
    assert writable == [vendor_engine.ACTIVE]


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
    ("reason", "status"),
    [(Reason.VENDOR_CLOSED, 401), (Reason.VENDOR_SUSPENDED, 403), (Reason.VENDOR_NOT_ACTIVE, 403)],
)
def test_vendor_refusals_are_problems_with_their_own_code(reason: Reason, status: int) -> None:
    body = auth_problem(deny(reason), "catalog:write").to_dict()
    assert body["status"] == status
    assert body["code"] == str(reason)
    assert body["detail"]
