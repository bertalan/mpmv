"""VEN.40: a vendor's return-policy data, versioned and staff-reviewed (VEN.35 pattern)."""

from typing import Any

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from apps.accounts.tests.factories import UserFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.vendors import return_services as rs
from apps.vendors.models import ReturnPolicyVersion
from apps.vendors.tests.factories import (
    ReturnPolicyVersionFactory,
    VendorFactory,
    VendorMemberFactory,
)

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="user:test", source="vendor")


def values(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "return_window_days": 30,
        "return_shipping_paid_by": "vendor",
        "return_methods": ["post"],
        "returns_page_url": "https://shop.example/returns",
        "return_country": "IT",
    }
    return base | over


def owner_of(vendor: Any) -> Any:
    return VendorMemberFactory.create(vendor=vendor, role="owner").user


def staff() -> Any:
    return UserFactory.create(is_staff=True, permissions=["vendors.change_vendor"])


def test_13_days_is_refused_and_14_is_accepted() -> None:
    vendor = VendorFactory.create()
    actor = owner_of(vendor)
    with pytest.raises(ValidationError):
        rs.propose_return_policy(vendor, values(return_window_days=13), actor=actor, ctx=CTX)
    assert not ReturnPolicyVersion.objects.exists()
    rs.propose_return_policy(vendor, values(return_window_days=14), actor=actor, ctx=CTX)
    assert ReturnPolicyVersion.objects.get().return_window_days == 14


def test_the_database_refuses_a_window_under_14_and_a_non_https_page() -> None:
    vendor = VendorFactory.create()
    with pytest.raises(IntegrityError), transaction.atomic():
        ReturnPolicyVersionFactory.create(vendor=vendor, return_window_days=13)
    with pytest.raises(IntegrityError), transaction.atomic():
        ReturnPolicyVersionFactory.create(vendor=vendor, returns_page_url="http://x.example/r")


def test_an_unapproved_edit_is_not_public_until_staff_approve() -> None:
    vendor = VendorFactory.create()
    actor = owner_of(vendor)
    rs.propose_return_policy(vendor, values(return_window_days=30), actor=actor, ctx=CTX)
    assert rs.public_return_policy(vendor) is None
    version = ReturnPolicyVersion.objects.get()
    rs.review_return_policy(version, approve=True, actor=staff(), ctx=CTX)
    public = rs.public_return_policy(vendor)
    assert public is not None
    assert public.return_window_days == 30
    assert public.return_shipping_paid_by == "vendor"
    assert public.return_methods == ("post",)
    assert public.is_default is False
    # A later edit stays private; the approved one keeps being the public one.
    rs.propose_return_policy(vendor, values(return_window_days=60), actor=actor, ctx=CTX)
    still = rs.public_return_policy(vendor)
    assert still is not None
    assert still.return_window_days == 30


def test_a_rejected_proposal_is_never_public_and_keeps_the_previous_approved() -> None:
    vendor = VendorFactory.create()
    ReturnPolicyVersionFactory.create(vendor=vendor, return_window_days=21, status="approved")
    rs.propose_return_policy(vendor, values(return_window_days=90), actor=owner_of(vendor), ctx=CTX)
    pending = ReturnPolicyVersion.objects.get(status="pending")
    rs.review_return_policy(pending, approve=False, actor=staff(), ctx=CTX, note="No.")
    public = rs.public_return_policy(vendor)
    assert public is not None
    assert public.return_window_days == 21


def test_the_accessor_returns_none_without_data_and_effective_falls_back_to_the_default() -> None:
    vendor = VendorFactory.create(country="DE")
    assert rs.public_return_policy(vendor) is None
    effective = rs.effective_return_policy(vendor)
    assert effective.is_default is True
    assert effective.return_window_days == 14
    assert effective.return_shipping_paid_by == "customer"
    assert effective.return_country == "DE"


def test_a_new_proposal_withdraws_the_previous_pending_one() -> None:
    vendor = VendorFactory.create()
    actor = owner_of(vendor)
    rs.propose_return_policy(vendor, values(), actor=actor, ctx=CTX)
    rs.propose_return_policy(vendor, values(return_window_days=40), actor=actor, ctx=CTX)
    assert ReturnPolicyVersion.objects.filter(vendor=vendor, status="pending").count() == 1
    assert ReturnPolicyVersion.objects.filter(vendor=vendor).count() == 2


def test_only_the_owner_proposes_and_only_staff_review() -> None:
    vendor = VendorFactory.create()
    member = VendorMemberFactory.create(vendor=vendor, role="staff").user
    with pytest.raises(PermissionDenied):
        rs.propose_return_policy(vendor, values(), actor=member, ctx=CTX)
    other_owner = owner_of(VendorFactory.create())
    with pytest.raises(PermissionDenied):
        rs.propose_return_policy(vendor, values(), actor=other_owner, ctx=CTX)
    version = ReturnPolicyVersionFactory.create(vendor=vendor)
    with pytest.raises(PermissionDenied):
        rs.review_return_policy(version, approve=True, actor=owner_of(vendor), ctx=CTX)
    version.refresh_from_db()
    assert version.status == "pending"


def test_dry_run_changes_nothing_and_returns_the_changeset() -> None:
    vendor = VendorFactory.create()
    changes = rs.propose_return_policy(
        vendor, values(), actor=owner_of(vendor), ctx=CTX, dry_run=True
    )
    assert changes
    assert not ReturnPolicyVersion.objects.exists()
    assert not AuditLog.objects.filter(action=rs.PROPOSED).exists()
    version = ReturnPolicyVersionFactory.create(vendor=vendor)
    rs.review_return_policy(version, approve=True, actor=staff(), ctx=CTX, dry_run=True)
    version.refresh_from_db()
    assert version.status == "pending"


def test_every_write_is_audited() -> None:
    vendor = VendorFactory.create()
    rs.propose_return_policy(vendor, values(), actor=owner_of(vendor), ctx=CTX)
    rs.review_return_policy(ReturnPolicyVersion.objects.get(), approve=True, actor=staff(), ctx=CTX)
    assert AuditLog.objects.filter(action=rs.PROPOSED).count() == 1
    assert AuditLog.objects.filter(action=rs.APPROVED).count() == 1


def test_a_suspended_vendor_cannot_propose() -> None:
    vendor = VendorFactory.create(status="suspended")
    with pytest.raises(PermissionDenied):
        rs.propose_return_policy(vendor, values(), actor=owner_of(vendor), ctx=CTX)


def test_a_stored_version_keeps_its_values_when_reviewed() -> None:
    vendor = VendorFactory.create()
    version = ReturnPolicyVersionFactory.create(vendor=vendor, return_window_days=45)
    rs.review_return_policy(version, approve=True, actor=staff(), ctx=CTX)
    version.refresh_from_db()
    assert (version.status, version.return_window_days) == ("approved", 45)
    assert version.reviewed_by is not None
    assert version.reviewed_at is not None
