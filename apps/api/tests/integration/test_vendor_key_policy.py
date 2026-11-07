"""CORE.14: the vendor API key policy, end to end.

- A vendor key cannot be issued with a platform-only scope.
- A suspended vendor's key reads (200) and is refused on writes (403), dry-runs included.
- A closed vendor's key is refused (401).
- Deleting a vendor writes one audit entry per key deleted with it.

The vendor status is read on every request, so reinstating a vendor gives its keys back their
writes without touching the keys.
"""

import datetime as dt
from typing import Any

import pytest
from django.conf import settings
from django.test import Client
from django.utils import timezone

from apps.accounts.tests.factories import UserFactory
from apps.api.engine import InvalidApiClient, Reason
from apps.api.models import ApiClient, ApiMutationLog
from apps.api.services import IssuedKey, authenticate, issue_api_client
from apps.api.tests.factories import ApiClientFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.vendors.models import Vendor, VendorMember
from apps.vendors.services import DELETED, delete_vendor
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = [pytest.mark.django_db, pytest.mark.urls("apps.api.tests.integration.urls_v1")]

SYSTEM = AuditContext(actor_label="system:test", source="system")
ME = "/api/v1/me"  # a read open to any valid key
ECHO = "/api/v1-test/test/echo"  # a write that needs test:write
CLIENT_MODEL = "api.apiclient"
HOOK = "apps.api.services.vendor_key_deletions"


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _key_of(status: str, scopes: list[str] | None = None) -> str:
    vendor = VendorFactory.create(status=status)
    _, key = ApiClientFactory.create_with_key(vendor=vendor, scopes=scopes or ["test:write"])
    return key


def _write(client: Client, key: str, *, dry_run: bool = False) -> Any:
    url = f"{ECHO}?dry_run=true" if dry_run else ECHO
    return client.post(
        url,
        data={"name": "Item", "quantity": 1},
        content_type="application/json",
        headers=_bearer(key),
    )


def _issue(vendor: Vendor, scopes: list[str], **kwargs: Any) -> IssuedKey:
    owner = VendorMemberFactory.create(vendor=vendor, role=VendorMember.Role.OWNER).user
    return issue_api_client(
        name="Vendor feed",
        scopes=scopes,
        expires_at=timezone.now() + dt.timedelta(days=90),
        vendor=vendor,
        issuer=owner,
        ctx=SYSTEM,
        **kwargs,
    )


# --- Issuance: vendor-grantable scopes only


@pytest.mark.parametrize("dry_run", [False, True])
def test_a_vendor_key_cannot_be_issued_with_a_platform_only_scope(dry_run: bool) -> None:
    vendor = VendorFactory.create()
    with pytest.raises(InvalidApiClient, match="platform-only"):
        _issue(vendor, ["catalog:write", "clients:write"], dry_run=dry_run)
    assert not ApiClient.objects.filter(vendor=vendor).exists()
    assert not AuditLog.objects.filter(target_model=CLIENT_MODEL).exists()


def test_platform_staff_cannot_grant_a_platform_only_scope_to_a_vendor_key_either() -> None:
    staff = UserFactory.create(staff=True, permissions=["api.add_apiclient"])
    with pytest.raises(InvalidApiClient, match="platform-only"):
        issue_api_client(
            name="Vendor feed",
            scopes=["clients:write"],
            expires_at=timezone.now() + dt.timedelta(days=90),
            vendor=VendorFactory.create(),
            issuer=staff,
            ctx=SYSTEM,
        )


def test_a_vendor_key_is_issued_with_vendor_grantable_scopes() -> None:
    issued = _issue(VendorFactory.create(), ["catalog:write", "events:read"])
    assert issued.client is not None
    assert issued.client.scopes == ["catalog:write", "events:read"]


def test_a_platform_key_may_hold_a_platform_only_scope() -> None:
    issued = issue_api_client(
        name="Ops pipeline",
        scopes=["clients:write"],
        expires_at=timezone.now() + dt.timedelta(days=90),
        ctx=SYSTEM,
    )
    assert issued.client is not None
    assert issued.client.scopes == ["clients:write"]


# --- Suspended vendor: reads only


def test_a_suspended_vendors_key_reads(client: Client) -> None:
    response = client.get(ME, headers=_bearer(_key_of(Vendor.Status.SUSPENDED)))
    assert response.status_code == 200


def test_a_suspended_vendors_key_gets_403_on_writes(client: Client) -> None:
    response = _write(client, _key_of(Vendor.Status.SUSPENDED))
    assert response.status_code == 403
    assert response["Content-Type"] == "application/problem+json"
    assert response.json()["code"] == Reason.VENDOR_SUSPENDED


def test_a_suspended_vendors_key_gets_403_on_dry_run_writes_too(client: Client) -> None:
    assert _write(client, _key_of(Vendor.Status.SUSPENDED), dry_run=True).status_code == 403


def test_a_refused_write_of_a_suspended_vendors_key_is_in_the_call_log(client: Client) -> None:
    _write(client, _key_of(Vendor.Status.SUSPENDED))
    assert ApiMutationLog.objects.get().status_code == 403


def test_an_active_vendors_key_writes(client: Client) -> None:
    assert _write(client, _key_of(Vendor.Status.ACTIVE)).status_code == 200


def test_reinstating_a_vendor_gives_its_keys_their_writes_back(client: Client) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.SUSPENDED)
    _, key = ApiClientFactory.create_with_key(vendor=vendor, scopes=["test:write"])
    assert _write(client, key).status_code == 403

    Vendor.objects.filter(pk=vendor.pk).update(status=Vendor.Status.ACTIVE)
    assert _write(client, key).status_code == 200


# --- Closed vendor: refused


def test_a_closed_vendors_key_gets_401_on_reads(client: Client) -> None:
    response = client.get(ME, headers=_bearer(_key_of(Vendor.Status.CLOSED)))
    assert response.status_code == 401
    assert response.json()["code"] == Reason.VENDOR_CLOSED
    assert 'error="invalid_token"' in response["WWW-Authenticate"]


def test_a_closed_vendors_key_gets_401_on_writes(client: Client) -> None:
    assert _write(client, _key_of(Vendor.Status.CLOSED)).status_code == 401


def test_a_closed_vendors_key_is_not_marked_as_used() -> None:
    vendor = VendorFactory.create(status=Vendor.Status.CLOSED)
    api_client, key = ApiClientFactory.create_with_key(vendor=vendor)
    result = authenticate(f"Bearer {key}", ip=None, required_scope=None, write=False)
    assert result.http_status == 401
    assert result.client is None
    api_client.refresh_from_db()
    assert api_client.last_used_at is None


def test_authenticate_treats_a_call_as_a_write_unless_told_otherwise() -> None:
    vendor = VendorFactory.create(status=Vendor.Status.SUSPENDED)
    _, key = ApiClientFactory.create_with_key(vendor=vendor)
    assert authenticate(f"Bearer {key}", ip=None, required_scope=None).http_status == 403
    assert authenticate(f"Bearer {key}", ip=None, required_scope=None, write=False).ok


# --- Vendor deletion: one audit entry per key


def test_the_vendor_deletion_hook_for_api_keys_is_configured() -> None:
    assert HOOK in settings.VENDOR_DELETION_AUDIT_HOOKS


def test_deleting_a_vendor_writes_one_audit_entry_per_key() -> None:
    vendor = VendorFactory.create()
    keys = ApiClientFactory.create_batch(2, vendor=vendor)
    revoked = ApiClientFactory.create(vendor=vendor, revoked=True)
    ApiClientFactory.create()  # a platform key
    ApiClientFactory.create(vendor_key=True)  # another vendor's key

    delete_vendor(vendor, ctx=SYSTEM)

    entries = AuditLog.objects.filter(target_model=CLIENT_MODEL)
    assert sorted(e.target_key for e in entries) == sorted(
        str(k.public_id) for k in [*keys, revoked]
    )
    for entry in entries:
        assert entry.action == DELETED
        assert entry.after is None
        assert isinstance(entry.before, dict)
        assert entry.before["vendor"] == str(vendor.public_id)
        assert entry.vendor_key == str(vendor.public_id)
        assert entry.actor_label == SYSTEM.actor_label
        assert "key_hash" not in entry.before
    assert ApiClient.objects.count() == 2


def test_a_dry_run_vendor_deletion_lists_its_keys_and_changes_nothing() -> None:
    vendor = VendorFactory.create()
    key = ApiClientFactory.create(vendor=vendor)

    changes = delete_vendor(vendor, ctx=SYSTEM, dry_run=True)

    deleted = [c for c in changes.changes if c.model == CLIENT_MODEL]
    assert [(c.op, c.key) for c in deleted] == [("delete", str(key.public_id))]
    assert ApiClient.objects.filter(pk=key.pk).exists()
    assert not AuditLog.objects.exists()


def test_deleting_a_vendor_without_keys_audits_no_key() -> None:
    delete_vendor(VendorFactory.create(), ctx=SYSTEM)
    assert not AuditLog.objects.filter(target_model=CLIENT_MODEL).exists()
