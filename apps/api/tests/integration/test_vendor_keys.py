"""CORE.13: vendor-bound API keys (`ApiClient.vendor`; null = platform key).

A vendor key acts for its vendor only, through the CORE.3 scoping (`for_actor`). Only platform
staff holding `api.add_apiclient` or an owner of that vendor may issue one, and the issuance is
audited with the vendor.
"""

import datetime as dt
from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.utils import timezone

from apps.accounts.tests.factories import UserFactory
from apps.api.models import ApiClient
from apps.api.services import IssuedKey, issue_api_client, revoke_api_client, rotate_api_key
from apps.api.tests.factories import ApiClientFactory
from apps.core.audit import AuditContext, user_context
from apps.core.models import AuditLog
from apps.vendors.models import Vendor, VendorMember
from apps.vendors.scoping import get_for_actor_or_404, is_vendor_scoped, scope_target
from apps.vendors.services import delete_vendor
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db

SCOPES = ["catalog:write", "stock:write"]
SYSTEM = AuditContext(actor_label="system:test", source="system")
ADD_PERMISSION = "api.add_apiclient"


def _issue(**kwargs: Any) -> IssuedKey:
    return issue_api_client(
        name="Vendor feed",
        scopes=SCOPES,
        expires_at=timezone.now() + dt.timedelta(days=90),
        ctx=kwargs.pop("ctx", SYSTEM),
        **kwargs,
    )


def _owner(vendor: Vendor) -> VendorMember:
    return VendorMemberFactory.create(vendor=vendor, role=VendorMember.Role.OWNER)


# --- Model


def test_a_client_is_a_platform_key_by_default() -> None:
    client = ApiClientFactory.create()
    assert client.vendor is None
    assert client.credential_vendor_id is None


def test_the_vendor_key_trait_binds_the_client_to_a_new_vendor() -> None:
    client = ApiClientFactory.create(vendor_key=True)
    assert isinstance(client.vendor, Vendor)
    assert client.credential_vendor_id == client.vendor.pk


def test_api_clients_are_vendor_scoped_through_their_vendor() -> None:
    assert is_vendor_scoped(ApiClient)
    assert scope_target(ApiClient) is Vendor


def test_a_vendor_member_lists_only_their_vendors_keys_and_never_platform_keys() -> None:
    owner_a = _owner(VendorFactory.create())
    key_a = ApiClientFactory.create(vendor=owner_a.vendor)
    ApiClientFactory.create(vendor_key=True)
    ApiClientFactory.create()

    assert list(ApiClient.objects.for_member(owner_a.user)) == [key_a]
    assert list(ApiClient.objects.for_actor(key_a)) == [key_a]
    assert ApiClient.objects.for_actor(UserFactory.create(staff=True)).count() == 3


def test_a_vendor_keys_actor_scope_is_its_vendor() -> None:
    key_a = ApiClientFactory.create(vendor_key=True)
    vendor_b = VendorFactory.create()
    assert key_a.vendor is not None

    assert list(Vendor.objects.for_actor(key_a)) == [key_a.vendor]
    assert get_for_actor_or_404(Vendor, key_a, pk=key_a.vendor.pk) == key_a.vendor
    with pytest.raises(Http404):
        get_for_actor_or_404(Vendor, key_a, pk=vendor_b.pk)


def test_a_platform_keys_actor_scope_is_every_vendor() -> None:
    vendors = {VendorFactory.create(), VendorFactory.create()}
    assert set(Vendor.objects.for_actor(ApiClientFactory.create())) == vendors


def test_deleting_a_vendor_deletes_its_keys_and_never_turns_them_into_platform_keys() -> None:
    key = ApiClientFactory.create(vendor_key=True)
    platform = ApiClientFactory.create()
    assert key.vendor is not None
    delete_vendor(key.vendor, ctx=SYSTEM)

    assert not ApiClient.objects.filter(pk=key.pk).exists()
    assert ApiClient.objects.filter(pk=platform.pk).exists()


# --- Issuance: who may issue which key


def _issuing_staff(**kwargs: Any) -> Any:
    """Platform staff holding `api.add_apiclient`: the permission that lets staff issue keys."""
    return UserFactory.create(staff=True, permissions=[ADD_PERMISSION], **kwargs)


def test_platform_staff_issue_a_vendor_key() -> None:
    vendor = VendorFactory.create()
    staff = _issuing_staff()

    issued = _issue(vendor=vendor, issuer=staff, ctx=user_context(staff, "127.0.0.1"))

    assert issued.client is not None
    assert issued.key is not None
    assert ApiClient.objects.get().vendor == vendor


def test_a_superuser_issues_platform_and_vendor_keys() -> None:
    superuser = UserFactory.create(superuser=True)
    assert _issue(issuer=superuser).client is not None
    assert _issue(vendor=VendorFactory.create(), issuer=superuser).client is not None


@pytest.mark.parametrize("permissions", [[], ["api.view_apiclient", "api.change_apiclient"]])
@pytest.mark.parametrize("key", ["platform", "vendor"])
def test_staff_without_the_add_permission_issue_no_key(key: str, permissions: list[str]) -> None:
    """`is_staff` alone (admin login) is not a right to mint a cross-tenant credential."""
    staff = UserFactory.create(staff=True, permissions=permissions)
    vendor = VendorFactory.create() if key == "vendor" else None

    with pytest.raises(PermissionDenied):
        _issue(vendor=vendor, issuer=staff)
    with pytest.raises(PermissionDenied):
        _issue(vendor=vendor, issuer=staff, dry_run=True)
    assert not ApiClient.objects.exists()
    assert not AuditLog.objects.filter(action="api.client.issued").exists()


def test_staff_without_the_add_permission_still_issue_keys_of_a_vendor_they_own() -> None:
    owner = _owner(VendorFactory.create())
    owner.user.is_staff = True
    owner.user.save(update_fields=["is_staff"])

    assert _issue(vendor=owner.vendor, issuer=owner.user).client is not None
    with pytest.raises(PermissionDenied):
        _issue(vendor=VendorFactory.create(), issuer=owner.user)
    with pytest.raises(PermissionDenied):
        _issue(issuer=owner.user)


def test_an_owner_issues_a_key_for_their_own_vendor() -> None:
    owner = _owner(VendorFactory.create())
    issued = _issue(vendor=owner.vendor, issuer=owner.user)
    assert issued.client is not None
    assert issued.client.vendor == owner.vendor


@pytest.mark.parametrize(
    "who",
    ["vendor staff member", "owner of another vendor", "customer", "inactive owner", "anonymous"],
)
def test_nobody_else_issues_a_vendor_key(who: str) -> None:
    vendor = VendorFactory.create()
    issuer: Any
    if who == "vendor staff member":
        issuer = VendorMemberFactory.create(vendor=vendor, role=VendorMember.Role.STAFF).user
    elif who == "owner of another vendor":
        issuer = _owner(VendorFactory.create()).user
    elif who == "customer":
        issuer = UserFactory.create()
    elif who == "inactive owner":
        issuer = _owner(vendor).user
        issuer.is_active = False
        issuer.save(update_fields=["is_active"])
    else:
        issuer = AnonymousUser()

    with pytest.raises(PermissionDenied):
        _issue(vendor=vendor, issuer=issuer)
    assert not ApiClient.objects.exists()
    assert not AuditLog.objects.filter(action="api.client.issued").exists()


def test_inactive_staff_do_not_issue_vendor_keys() -> None:
    staff = _issuing_staff(is_active=False)
    with pytest.raises(PermissionDenied):
        _issue(vendor=VendorFactory.create(), issuer=staff)


def test_a_vendor_key_needs_an_issuer() -> None:
    with pytest.raises(PermissionDenied):
        _issue(vendor=VendorFactory.create())
    assert not ApiClient.objects.exists()


def test_only_platform_staff_issue_platform_keys() -> None:
    owner = _owner(VendorFactory.create())
    with pytest.raises(PermissionDenied):
        _issue(issuer=owner.user)
    assert _issue(issuer=_issuing_staff()).client is not None


def test_a_platform_key_issued_by_the_system_needs_no_issuer() -> None:
    """Shell and deploy scripts (CORE.6 behaviour, unchanged)."""
    issued = _issue()
    assert issued.client is not None
    assert issued.client.vendor is None


def test_a_dry_run_vendor_key_checks_the_issuer_and_creates_nothing() -> None:
    owner = _owner(VendorFactory.create())
    issued = _issue(vendor=owner.vendor, issuer=owner.user, dry_run=True)
    assert issued.changeset.summary()["create"] == 1
    assert not ApiClient.objects.exists()
    with pytest.raises(PermissionDenied):
        _issue(vendor=VendorFactory.create(), issuer=owner.user, dry_run=True)


# --- Audit


def test_issuing_a_vendor_key_is_audited_with_the_issuer_and_the_vendor() -> None:
    owner = _owner(VendorFactory.create())
    issued = _issue(vendor=owner.vendor, issuer=owner.user, ctx=user_context(owner.user, None))
    assert issued.client is not None

    entry = AuditLog.objects.get(action="api.client.issued")
    assert entry.actor_id == owner.user.pk
    assert entry.target_key == str(issued.client.public_id)
    assert entry.vendor_key == str(owner.vendor.public_id)
    assert isinstance(entry.after, dict)
    assert entry.after["vendor"] == str(owner.vendor.public_id)


def test_a_platform_key_is_audited_without_a_vendor() -> None:
    _issue()
    entry = AuditLog.objects.get(action="api.client.issued")
    assert entry.vendor_key == ""
    assert isinstance(entry.after, dict)
    assert entry.after["vendor"] is None


def test_rotating_and_revoking_a_vendor_key_are_audited_under_its_vendor() -> None:
    client = ApiClientFactory.create(vendor_key=True)
    assert client.vendor is not None
    rotate_api_key(client, ctx=SYSTEM)
    revoke_api_client(client, ctx=SYSTEM)

    vendor_keys = set(
        AuditLog.objects.filter(target_key=str(client.public_id)).values_list(
            "vendor_key", flat=True
        )
    )
    assert vendor_keys == {str(client.vendor.public_id)}
