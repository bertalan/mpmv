"""CORE.18 / CORE.23: a platform owner made by `bootstrap_platform_owner` holds the owner role.

The command grants `core.platform_owner` directly to the user, with no "Platform: owner" group.
Every matrix check (the section check other apps reach through `settings.PLATFORM_SECTION_CHECK`,
the Manager menu, the help-entry policy) must count that direct grant as the OWNER role, while a
technical superuser without the permission still holds no section.
"""

from io import StringIO
from typing import Any

import pytest
from django.core.exceptions import PermissionDenied
from django.core.management import call_command

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.cms.models import HelpEntry
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.manager.access import may_access_section
from apps.manager.wagtail_hooks import HelpEntryPermissionPolicy
from apps.vendors import return_services as rs
from apps.vendors import services as vendor_services
from apps.vendors.models import ReturnPolicyVersion, VendorClaim
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db

SECTIONS = ("platform.vendor_approval", "platform.return_policy")


def bootstrapped_owner() -> Any:
    """The STAGE case: an account that became owner only through the bootstrap command."""
    user = UserFactory.create(staff=True)
    call_command("bootstrap_platform_owner", user.email, stdout=StringIO())
    return User.objects.get(pk=user.pk)


def ctx_of(user: Any) -> AuditContext:
    return AuditContext(actor_id=user.pk, actor_label=f"user:{user.pk}", source="admin")


def pending_return_policy() -> ReturnPolicyVersion:
    vendor = VendorFactory.create()
    owner = VendorMemberFactory.create(vendor=vendor, role="owner").user
    values = {
        "return_window_days": 30,
        "return_shipping_paid_by": "vendor",
        "return_methods": ["post"],
        "returns_page_url": "https://shop.example/returns",
        "return_country": "IT",
    }
    rs.propose_return_policy(vendor, values, actor=owner, ctx=ctx_of(owner))
    return ReturnPolicyVersion.objects.get()


def submitted_claim() -> VendorClaim:
    vendor = VendorFactory.create(italian_company=True, contact_email="legal@vendor-a.example")
    claimant = UserFactory.create(email="legal@vendor-a.example")
    return VendorClaim.objects.create(
        vendor=vendor, status=VendorClaim.Status.SUBMITTED, claimant=claimant
    )


@pytest.mark.parametrize("section", SECTIONS)
def test_a_bootstrapped_owner_writes_the_approval_sections(section: str) -> None:
    owner = bootstrapped_owner()
    assert not owner.groups.exists()  # the direct grant only, as on STAGE
    assert may_access_section(owner, section, write=True)


@pytest.mark.parametrize("section", SECTIONS)
def test_a_superuser_without_the_owner_permission_is_still_refused(section: str) -> None:
    superuser = UserFactory.create(superuser=True)
    assert not may_access_section(superuser, section)
    assert not may_access_section(superuser, section, write=True)


def test_a_bootstrapped_owner_approves_a_return_policy() -> None:
    version = pending_return_policy()
    owner = bootstrapped_owner()
    assert vendor_services.can_approve_return_policy(owner)
    rs.review_return_policy(version, approve=True, actor=owner, ctx=ctx_of(owner))
    assert ReturnPolicyVersion.objects.get().status == "approved"


def test_a_superuser_without_the_owner_permission_cannot_approve_a_return_policy() -> None:
    version = pending_return_policy()
    superuser = UserFactory.create(superuser=True)
    with pytest.raises(PermissionDenied):
        rs.review_return_policy(version, approve=True, actor=superuser, ctx=ctx_of(superuser))
    assert ReturnPolicyVersion.objects.get().status == "pending"


def test_a_bootstrapped_owner_approves_a_vendor_claim() -> None:
    claim = submitted_claim()
    owner = bootstrapped_owner()
    assert vendor_services.may_approve_claims(owner)
    vendor_services.approve_claim(claim, ctx=ctx_of(owner))
    claim.refresh_from_db()
    assert claim.status == VendorClaim.Status.APPROVED
    assert AuditLog.objects.filter(actor_id=owner.pk).exists()


def test_a_superuser_without_the_owner_permission_cannot_approve_a_vendor_claim() -> None:
    claim = submitted_claim()
    superuser = UserFactory.create(superuser=True)
    with pytest.raises(PermissionDenied):
        vendor_services.approve_claim(claim, ctx=ctx_of(superuser))
    claim.refresh_from_db()
    assert claim.status == VendorClaim.Status.SUBMITTED


def test_the_help_entry_policy_reads_the_owner_cell_for_a_bootstrapped_owner() -> None:
    policy = HelpEntryPermissionPolicy(HelpEntry)
    owner = bootstrapped_owner()
    assert policy.user_has_permission(owner, "view")  # owner reads platform.moderation
    assert not policy.user_has_permission(owner, "change")  # read-only for the owner
