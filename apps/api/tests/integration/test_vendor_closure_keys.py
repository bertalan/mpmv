"""CORE.14 + VEN.21: closing a vendor revokes its API keys; reopening never brings one back.

API keys work only for active vendors. Closing a vendor revokes every key it still holds, each
with its own `api.client.revoked` audit entry, listed in the closure's ChangeSet (dry-run too).
A reopened vendor is onboarding with no working key: once active again, an owner issues new
keys. A key left active by a closure from before this rule is revoked by the reopen.
"""

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.test import Client

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.api.engine import InvalidApiClient, Reason
from apps.api.models import ApiClient
from apps.api.services import rotate_api_key
from apps.api.tests.factories import ApiClientFactory
from apps.api.tests.integration.v1_api import VENDOR_SCOPE
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.vendors.models import Vendor, VendorMember
from apps.vendors.services import change_status, reopen_vendor
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = [pytest.mark.django_db, pytest.mark.urls("apps.api.tests.integration.urls_v1")]

SYSTEM = AuditContext(actor_label="system:test", source="system")
ME = "/api/v1/me"
ECHO = "/api/v1-test/test/vendor/echo"
CLIENT_MODEL = "api.apiclient"
REVOKED = "api.client.revoked"
REASON = "The owner asked to sell again"


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _staff() -> User:
    return UserFactory.create(staff=True, permissions=["vendors.change_vendor"])


def _vendor_with_owner(status: str) -> Vendor:
    vendor = VendorFactory.create(status=status)
    VendorMemberFactory.create(vendor=vendor, role=VendorMember.Role.OWNER)
    return vendor


def _key(vendor: Vendor) -> tuple[ApiClient, str]:
    return ApiClientFactory.create_with_key(vendor=vendor, scopes=[VENDOR_SCOPE])


@pytest.mark.parametrize("status", [Vendor.Status.ACTIVE, Vendor.Status.SUSPENDED])
def test_closing_a_vendor_revokes_each_of_its_active_keys_with_an_audit_entry(
    status: str,
) -> None:
    vendor = _vendor_with_owner(status)
    first, _ = _key(vendor)
    second, _ = _key(vendor)
    already = ApiClientFactory.create(vendor=vendor, revoked=True)
    other = ApiClientFactory.create(vendor_key=True)

    changes = change_status(vendor, Vendor.Status.CLOSED, ctx=SYSTEM)

    revoked_keys = sorted(str(c.public_id) for c in (first, second))
    assert sorted(c.key for c in changes.changes if c.model == CLIENT_MODEL) == revoked_keys
    for client in (first, second):
        client.refresh_from_db()
        assert client.is_active is False
        assert client.revoked_at is not None
    entries = AuditLog.objects.filter(target_model=CLIENT_MODEL, action=REVOKED)
    assert sorted(entries.values_list("target_key", flat=True)) == revoked_keys
    assert set(entries.values_list("vendor_key", flat=True)) == {str(vendor.public_id)}
    already_revoked_at = already.revoked_at
    already.refresh_from_db()
    assert already.revoked_at == already_revoked_at  # an already revoked key is left alone
    other.refresh_from_db()
    assert other.is_active is True  # another vendor's key is untouched


def test_a_closure_dry_run_lists_the_revocations_and_applies_nothing() -> None:
    vendor = _vendor_with_owner(Vendor.Status.ACTIVE)
    client, _ = _key(vendor)

    changes = change_status(vendor, Vendor.Status.CLOSED, ctx=SYSTEM, dry_run=True)

    revocation = next(c for c in changes.changes if c.model == CLIENT_MODEL)
    assert revocation.key == str(client.public_id)
    assert revocation.fields["is_active"] == (True, False)
    client.refresh_from_db()
    assert client.is_active is True
    assert not AuditLog.objects.exists()


def test_other_status_changes_leave_the_keys_alone() -> None:
    vendor = _vendor_with_owner(Vendor.Status.ACTIVE)
    client, _ = _key(vendor)

    changes = change_status(vendor, Vendor.Status.SUSPENDED, ctx=SYSTEM)

    assert all(c.model != CLIENT_MODEL for c in changes.changes)
    client.refresh_from_db()
    assert client.is_active is True


def test_a_reopened_vendor_gets_none_of_its_old_keys_back(client: Client) -> None:
    vendor = _vendor_with_owner(Vendor.Status.ACTIVE)
    api_client, key = _key(vendor)
    change_status(vendor, Vendor.Status.CLOSED, ctx=SYSTEM)

    reopen_vendor(vendor, actor=_staff(), reason=REASON, ctx=SYSTEM)

    api_client.refresh_from_db()
    assert api_client.is_active is False
    response = client.get(ME, headers=_bearer(key))
    assert response.status_code == 401
    assert response.json()["code"] == Reason.REVOKED
    write = client.post(
        ECHO,
        data={"name": "Item", "quantity": 1},
        content_type="application/json",
        headers=_bearer(key),
    )
    assert write.status_code == 401
    with pytest.raises(InvalidApiClient):
        rotate_api_key(api_client, ctx=SYSTEM)


def test_the_reopen_revokes_a_key_left_active_by_an_earlier_closure(client: Client) -> None:
    vendor = _vendor_with_owner(Vendor.Status.CLOSED)  # closed before keys were revoked
    api_client, key = _key(vendor)

    dry = reopen_vendor(vendor, actor=_staff(), reason=REASON, ctx=SYSTEM, dry_run=True)
    assert [c.key for c in dry.changes if c.model == CLIENT_MODEL] == [str(api_client.public_id)]
    api_client.refresh_from_db()
    assert api_client.is_active is True

    changes = reopen_vendor(vendor, actor=_staff(), reason=REASON, ctx=SYSTEM)

    assert [c.key for c in changes.changes if c.model == CLIENT_MODEL] == [
        str(api_client.public_id)
    ]
    api_client.refresh_from_db()
    assert api_client.is_active is False
    assert AuditLog.objects.filter(
        target_model=CLIENT_MODEL, target_key=str(api_client.public_id), action=REVOKED
    ).exists()
    assert client.get(ME, headers=_bearer(key)).status_code == 401


def test_a_refused_reopen_revokes_nothing() -> None:
    vendor = _vendor_with_owner(Vendor.Status.CLOSED)
    api_client, _ = _key(vendor)

    with pytest.raises(PermissionDenied):
        reopen_vendor(vendor, actor=AnonymousUser(), reason=REASON, ctx=SYSTEM)

    api_client.refresh_from_db()
    assert api_client.is_active is True
