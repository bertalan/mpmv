"""CORE.6: ApiClient issuance, hashing, scopes, expiry, rotation, revocation, IP allow-list."""

import datetime as dt
from typing import Any

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.api.engine import AuthStatus, InvalidApiClient, Reason
from apps.api.models import ApiClient
from apps.api.services import (
    authenticate,
    issue_api_client,
    revoke_api_client,
    rotate_api_key,
)
from apps.core.models import AuditLog

from .conftest import CTX, IssueFn

pytestmark = pytest.mark.django_db

SCOPE = "catalog:write"


def _bearer(key: str) -> str:
    return f"Bearer {key}"


def _payload(value: Any) -> dict[str, Any]:
    assert isinstance(value, dict)
    return value


def _stored_values(client: ApiClient) -> list[str]:
    row = ApiClient.objects.filter(pk=client.pk).values().get()
    return [str(v) for v in row.values()]


# --- Issuance


def test_issue_stores_only_prefix_and_hash_and_shows_the_key_once(issue: IssueFn) -> None:
    client, key = issue()
    prefix, secret = key.split("_", 2)[1:]
    assert client.key_prefix == prefix
    assert client.key_hash != key
    assert len(client.key_hash) == 64
    assert all(secret not in value for value in _stored_values(client))
    assert client.is_active
    assert client.scopes == ["catalog:write", "stock:write"]
    assert client.last_used_at is None


def test_issue_writes_an_audit_entry_without_hash_or_secret(issue: IssueFn) -> None:
    client, key = issue()
    entry = AuditLog.objects.get(action="api.client.issued")
    assert entry.target_model == "api.apiclient"
    assert entry.target_key == str(client.public_id)
    assert entry.actor_label == "staff:ops"
    assert entry.before is None
    after = _payload(entry.after)
    assert after["key_prefix"] == client.key_prefix
    assert after["scopes"] == ["catalog:write", "stock:write"]
    assert "key_hash" not in after
    assert key.split("_", 2)[2] not in str(after)


def test_issue_dry_run_creates_nothing() -> None:
    issued = issue_api_client(
        name="Dry",
        scopes=[SCOPE],
        expires_at=timezone.now() + dt.timedelta(days=1),
        ctx=CTX,
        dry_run=True,
    )
    assert issued.changeset.summary()["create"] == 1
    assert issued.client is None
    assert issued.key is None
    assert ApiClient.objects.count() == 0
    assert AuditLog.objects.count() == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"scopes": []},
        {"scopes": ["not a scope"]},
        {"expires_at": "past"},
        {"allowed_ips": ["not-an-ip"]},
        {"name": "  "},
    ],
)
def test_issue_refuses_invalid_input_and_writes_nothing(overrides: dict[str, object]) -> None:
    params: dict[str, object] = {
        "name": "Bad",
        "scopes": [SCOPE],
        "expires_at": timezone.now() + dt.timedelta(days=1),
    }
    params.update(overrides)
    if params["expires_at"] == "past":
        params["expires_at"] = timezone.now() - dt.timedelta(seconds=1)
    with pytest.raises(InvalidApiClient):
        issue_api_client(**params, ctx=CTX)  # type: ignore[arg-type]
    assert ApiClient.objects.count() == 0
    assert AuditLog.objects.count() == 0


def test_key_prefix_is_unique(issue: IssueFn) -> None:
    client, _ = issue()
    other, _ = issue(name="Other")
    with pytest.raises(IntegrityError), transaction.atomic():
        ApiClient.objects.filter(pk=other.pk).update(key_prefix=client.key_prefix)


# --- Authentication


def test_valid_key_authenticates_and_records_last_use(issue: IssueFn) -> None:
    client, key = issue()
    result = authenticate(_bearer(key), ip="203.0.113.7", required_scope=SCOPE)
    assert result.status == AuthStatus.OK
    assert result.http_status == 200
    assert result.client is not None
    assert result.client.pk == client.pk
    client.refresh_from_db()
    assert client.last_used_at is not None


def test_missing_header_is_401() -> None:
    result = authenticate(None, ip=None, required_scope=SCOPE)
    assert (result.http_status, result.reason) == (401, Reason.MISSING)
    assert result.client is None


def test_wrong_secret_for_an_existing_prefix_is_401(issue: IssueFn) -> None:
    client, key = issue()
    forged = key[:-4] + ("aaaa" if not key.endswith("aaaa") else "bbbb")
    result = authenticate(_bearer(forged), ip=None, required_scope=SCOPE)
    assert (result.http_status, result.reason) == (401, Reason.INVALID)
    assert result.client is None
    client.refresh_from_db()
    assert client.last_used_at is None


def test_unknown_prefix_is_401_with_the_same_reason_as_a_wrong_secret(issue: IssueFn) -> None:
    _, key = issue()
    ApiClient.objects.all().delete()
    result = authenticate(_bearer(key), ip=None, required_scope=SCOPE)
    assert (result.http_status, result.reason) == (401, Reason.INVALID)


def test_expired_key_is_401(issue: IssueFn) -> None:
    client, key = issue()
    ApiClient.objects.filter(pk=client.pk).update(
        expires_at=timezone.now() - dt.timedelta(minutes=1)
    )
    result = authenticate(_bearer(key), ip=None, required_scope=SCOPE)
    assert (result.http_status, result.reason) == (401, Reason.EXPIRED)
    assert result.client is None


def test_out_of_scope_key_is_403(issue: IssueFn) -> None:
    _, key = issue(scopes=["stock:write"])
    result = authenticate(_bearer(key), ip=None, required_scope=SCOPE)
    assert (result.http_status, result.reason) == (403, Reason.INSUFFICIENT_SCOPE)


def test_ip_outside_the_allow_list_is_403(issue: IssueFn) -> None:
    _, key = issue(allowed_ips=["198.51.100.0/24"])
    denied = authenticate(_bearer(key), ip="203.0.113.7", required_scope=SCOPE)
    allowed = authenticate(_bearer(key), ip="198.51.100.20", required_scope=SCOPE)
    assert (denied.http_status, denied.reason) == (403, Reason.IP_NOT_ALLOWED)
    assert allowed.http_status == 200


# --- Revocation


def test_revoked_key_is_401_and_revocation_is_audited(issue: IssueFn) -> None:
    client, key = issue()
    revoke_api_client(client, ctx=CTX)
    client.refresh_from_db()
    assert not client.is_active
    assert client.revoked_at is not None
    result = authenticate(_bearer(key), ip=None, required_scope=SCOPE)
    assert (result.http_status, result.reason) == (401, Reason.REVOKED)
    entry = AuditLog.objects.get(action="api.client.revoked")
    assert entry.target_key == str(client.public_id)
    assert _payload(entry.before)["is_active"] is True
    assert _payload(entry.after)["is_active"] is False


def test_revoking_twice_is_a_no_op(issue: IssueFn) -> None:
    client, _ = issue()
    revoke_api_client(client, ctx=CTX)
    second = revoke_api_client(client, ctx=CTX)
    assert second.is_empty
    assert AuditLog.objects.filter(action="api.client.revoked").count() == 1


def test_revoke_dry_run_changes_nothing(issue: IssueFn) -> None:
    client, key = issue()
    changeset = revoke_api_client(client, ctx=CTX, dry_run=True)
    assert changeset.summary()["update"] == 1
    client.refresh_from_db()
    assert client.is_active
    assert authenticate(_bearer(key), ip=None, required_scope=SCOPE).http_status == 200


# --- Rotation (the old key stops working immediately; no grace period)


def test_rotation_invalidates_the_old_key_immediately(issue: IssueFn) -> None:
    client, old_key = issue()
    rotated = rotate_api_key(client, ctx=CTX)
    assert rotated.key is not None
    assert rotated.key != old_key
    old = authenticate(_bearer(old_key), ip=None, required_scope=SCOPE)
    new = authenticate(_bearer(rotated.key), ip=None, required_scope=SCOPE)
    assert (old.http_status, old.reason) == (401, Reason.INVALID)
    assert new.http_status == 200
    assert new.client is not None
    assert new.client.public_id == client.public_id
    assert new.client.scopes == client.scopes


def test_rotation_is_audited_with_old_and_new_prefix(issue: IssueFn) -> None:
    client, _ = issue()
    old_prefix = client.key_prefix
    rotated = rotate_api_key(client, ctx=CTX)
    assert rotated.client is not None
    entry = AuditLog.objects.get(action="api.client.rotated")
    after = _payload(entry.after)
    assert _payload(entry.before)["key_prefix"] == old_prefix
    assert after["key_prefix"] == rotated.client.key_prefix != old_prefix
    assert "key_hash" not in after


def test_rotation_can_renew_an_expired_key(issue: IssueFn) -> None:
    client, _ = issue()
    ApiClient.objects.filter(pk=client.pk).update(expires_at=timezone.now() - dt.timedelta(days=1))
    client.refresh_from_db()
    new_expiry = timezone.now() + dt.timedelta(days=30)
    rotated = rotate_api_key(client, ctx=CTX, expires_at=new_expiry)
    assert rotated.key is not None
    assert authenticate(_bearer(rotated.key), ip=None, required_scope=SCOPE).http_status == 200


def test_revoked_clients_cannot_be_rotated(issue: IssueFn) -> None:
    client, _ = issue()
    revoke_api_client(client, ctx=CTX)
    with pytest.raises(InvalidApiClient):
        rotate_api_key(client, ctx=CTX)
    assert not AuditLog.objects.filter(action="api.client.rotated").exists()


def test_rotate_dry_run_keeps_the_old_key(issue: IssueFn) -> None:
    client, old_key = issue()
    rotated = rotate_api_key(client, ctx=CTX, dry_run=True)
    assert rotated.key is None
    assert rotated.changeset.summary()["update"] == 1
    assert authenticate(_bearer(old_key), ip=None, required_scope=SCOPE).http_status == 200
