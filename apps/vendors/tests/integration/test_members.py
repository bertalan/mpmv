"""CORE.2: VendorMember model, membership services and the per-role permission check."""

import pytest
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from apps.accounts.tests.factories import UserFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.vendors.engine import (
    AlreadyMember,
    NoOwnerLeft,
    UnknownAction,
    UnknownRole,
    VendorAction,
)
from apps.vendors.models import VendorMember
from apps.vendors.services import (
    MEMBER_ADDED,
    MEMBER_MODEL,
    MEMBER_REMOVED,
    MEMBER_ROLE_CHANGED,
    add_member,
    change_member_role,
    has_vendor_permission,
    member_role,
    remove_member,
)
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:reviewer", source="admin", ip="127.0.0.1")
OWNER = VendorMember.Role.OWNER
STAFF = VendorMember.Role.STAFF


# --- Model


def test_role_choices_match_the_engine() -> None:
    assert tuple(VendorMember.Role.values) == ("owner", "staff")


def test_member_links_a_user_to_a_vendor() -> None:
    member = VendorMemberFactory.create(role=STAFF)
    assert member.public_id is not None
    assert member.vendor.members.get() == member
    assert member.user.vendor_memberships.get() == member
    assert str(member) == f"{member.user} @ {member.vendor} (staff)"


def test_a_user_is_a_member_of_a_vendor_at_most_once() -> None:
    member = VendorMemberFactory.create()
    with pytest.raises(IntegrityError), transaction.atomic():
        VendorMember.objects.create(vendor=member.vendor, user=member.user, role=STAFF)


def test_a_user_can_belong_to_several_vendors() -> None:
    user = UserFactory.create()
    VendorMemberFactory.create(user=user)
    VendorMemberFactory.create(user=user, role=STAFF)
    assert user.vendor_memberships.count() == 2


def test_role_must_be_a_known_value_in_the_database() -> None:
    member = VendorMemberFactory.create()
    with pytest.raises(IntegrityError), transaction.atomic():
        VendorMember.objects.filter(pk=member.pk).update(role="admin")


def test_deleting_a_user_with_memberships_is_blocked() -> None:
    # Memberships go through `remove_member` (last-owner rule, audit); never a silent cascade.
    member = VendorMemberFactory.create()
    with pytest.raises(ProtectedError):
        member.user.delete()


def test_deleting_a_vendor_deletes_its_memberships() -> None:
    member = VendorMemberFactory.create()
    member.vendor.delete()
    assert not VendorMember.objects.exists()


# --- add_member


def test_first_member_added_as_owner_is_saved_and_audited() -> None:
    vendor = VendorFactory.create()
    user = UserFactory.create()

    changes = add_member(vendor, user, OWNER, ctx=CTX)

    member = VendorMember.objects.get()
    assert (member.vendor, member.user, member.role) == (vendor, user, OWNER)
    assert changes.summary() == {"create": 1, "update": 0, "delete": 0}
    entry = AuditLog.objects.get()
    assert entry.action == MEMBER_ADDED == "vendor.member_added"
    assert entry.target_model == MEMBER_MODEL == "vendors.vendormember"
    assert entry.target_key == str(member.public_id)
    assert entry.before is None
    assert entry.after == {
        "vendor": str(vendor.public_id),
        "user": str(user.public_id),
        "role": "owner",
    }
    assert entry.actor_label == "staff:reviewer"


def test_staff_member_can_join_a_vendor_that_has_an_owner() -> None:
    owner = VendorMemberFactory.create(role=OWNER)
    user = UserFactory.create()
    add_member(owner.vendor, user, STAFF, ctx=CTX)
    assert member_role(user, owner.vendor) == STAFF


def test_first_member_cannot_be_staff() -> None:
    vendor = VendorFactory.create()
    with pytest.raises(NoOwnerLeft):
        add_member(vendor, UserFactory.create(), STAFF, ctx=CTX)
    assert not VendorMember.objects.exists()
    assert not AuditLog.objects.exists()


def test_add_member_dry_run_returns_the_change_and_writes_nothing() -> None:
    vendor = VendorFactory.create()
    user = UserFactory.create()

    changes = add_member(vendor, user, OWNER, ctx=CTX, dry_run=True)

    assert changes.summary() == {"create": 1, "update": 0, "delete": 0}
    assert changes.changes[0].after == {
        "vendor": str(vendor.public_id),
        "user": str(user.public_id),
        "role": "owner",
    }
    assert not VendorMember.objects.exists()
    assert not AuditLog.objects.exists()


def test_adding_an_existing_member_is_refused() -> None:
    member = VendorMemberFactory.create(role=OWNER)
    with pytest.raises(AlreadyMember):
        add_member(member.vendor, member.user, STAFF, ctx=CTX)
    member.refresh_from_db()
    assert member.role == OWNER
    assert not AuditLog.objects.exists()


def test_add_member_refuses_an_unknown_role() -> None:
    with pytest.raises(UnknownRole):
        add_member(VendorFactory.create(), UserFactory.create(), "admin", ctx=CTX)
    assert not VendorMember.objects.exists()


# --- change_member_role


def test_promoting_staff_to_owner_is_saved_and_audited() -> None:
    VendorMemberFactory.create(role=OWNER)
    member = VendorMemberFactory.create(role=STAFF)

    changes = change_member_role(member, OWNER, ctx=CTX)

    assert changes.summary() == {"create": 0, "update": 1, "delete": 0}
    member.refresh_from_db()
    assert member.role == OWNER
    entry = AuditLog.objects.get()
    assert entry.action == MEMBER_ROLE_CHANGED == "vendor.member_role_changed"
    assert entry.target_key == str(member.public_id)
    assert entry.before["role"] == "staff"
    assert entry.after["role"] == "owner"


def test_demoting_an_owner_is_allowed_while_another_owner_remains() -> None:
    first = VendorMemberFactory.create(role=OWNER)
    VendorMemberFactory.create(vendor=first.vendor, role=OWNER)

    change_member_role(first, STAFF, ctx=CTX)

    first.refresh_from_db()
    assert first.role == STAFF


def test_demoting_the_last_owner_is_refused() -> None:
    owner = VendorMemberFactory.create(role=OWNER)
    VendorMemberFactory.create(vendor=owner.vendor, role=STAFF)

    with pytest.raises(NoOwnerLeft):
        change_member_role(owner, STAFF, ctx=CTX)
    with pytest.raises(NoOwnerLeft):
        change_member_role(owner, STAFF, ctx=CTX, dry_run=True)

    owner.refresh_from_db()
    assert owner.role == OWNER
    assert not AuditLog.objects.exists()


def test_change_role_dry_run_writes_nothing() -> None:
    owner = VendorMemberFactory.create(role=OWNER)
    member = VendorMemberFactory.create(vendor=owner.vendor, role=STAFF)

    changes = change_member_role(member, OWNER, ctx=CTX, dry_run=True)

    assert changes.summary() == {"create": 0, "update": 1, "delete": 0}
    member.refresh_from_db()
    assert member.role == STAFF
    assert not AuditLog.objects.exists()


def test_setting_the_same_role_is_a_no_op_without_audit() -> None:
    owner = VendorMemberFactory.create(role=OWNER)
    changes = change_member_role(owner, OWNER, ctx=CTX)
    assert changes.is_empty
    assert not AuditLog.objects.exists()


def test_role_change_is_checked_against_the_stored_role_not_a_stale_instance() -> None:
    first = VendorMemberFactory.create(role=OWNER)
    second = VendorMemberFactory.create(vendor=first.vendor, role=OWNER)
    stale_second = VendorMember.objects.get(pk=second.pk)
    change_member_role(first, STAFF, ctx=CTX)  # second is now the only owner

    with pytest.raises(NoOwnerLeft):
        change_member_role(stale_second, STAFF, ctx=CTX)


def test_change_role_refuses_an_unknown_role() -> None:
    member = VendorMemberFactory.create(role=OWNER)
    with pytest.raises(UnknownRole):
        change_member_role(member, "admin", ctx=CTX)


# --- remove_member


def test_removing_a_staff_member_is_saved_and_audited() -> None:
    owner = VendorMemberFactory.create(role=OWNER)
    member = VendorMemberFactory.create(vendor=owner.vendor, role=STAFF)
    key = str(member.public_id)

    changes = remove_member(member, ctx=CTX)

    assert changes.summary() == {"create": 0, "update": 0, "delete": 1}
    assert list(VendorMember.objects.all()) == [owner]
    entry = AuditLog.objects.get()
    assert entry.action == MEMBER_REMOVED == "vendor.member_removed"
    assert entry.target_key == key
    assert entry.before == {
        "vendor": str(owner.vendor.public_id),
        "user": str(member.user.public_id),
        "role": "staff",
    }
    assert entry.after is None


def test_removing_an_owner_is_allowed_while_another_owner_remains() -> None:
    first = VendorMemberFactory.create(role=OWNER)
    second = VendorMemberFactory.create(vendor=first.vendor, role=OWNER)
    remove_member(first, ctx=CTX)
    assert list(VendorMember.objects.all()) == [second]


@pytest.mark.parametrize("with_staff", [True, False])
def test_removing_the_last_owner_is_refused(with_staff: bool) -> None:
    owner = VendorMemberFactory.create(role=OWNER)
    if with_staff:
        VendorMemberFactory.create(vendor=owner.vendor, role=STAFF)

    with pytest.raises(NoOwnerLeft):
        remove_member(owner, ctx=CTX)
    with pytest.raises(NoOwnerLeft):
        remove_member(owner, ctx=CTX, dry_run=True)

    assert VendorMember.objects.filter(pk=owner.pk).exists()
    assert not AuditLog.objects.exists()


def test_remove_member_dry_run_writes_nothing() -> None:
    owner = VendorMemberFactory.create(role=OWNER)
    member = VendorMemberFactory.create(vendor=owner.vendor, role=STAFF)

    changes = remove_member(member, ctx=CTX, dry_run=True)

    assert changes.summary() == {"create": 0, "update": 0, "delete": 1}
    assert VendorMember.objects.count() == 2
    assert not AuditLog.objects.exists()


def test_owners_of_other_vendors_do_not_count() -> None:
    owner = VendorMemberFactory.create(role=OWNER)
    VendorMemberFactory.create(role=OWNER)  # owner of another vendor
    with pytest.raises(NoOwnerLeft):
        remove_member(owner, ctx=CTX)


# --- Permission check


def test_permission_follows_the_member_role() -> None:
    owner = VendorMemberFactory.create(role=OWNER)
    staff = VendorMemberFactory.create(vendor=owner.vendor, role=STAFF)
    vendor = owner.vendor

    assert has_vendor_permission(owner.user, vendor, VendorAction.BANK_ACCOUNT)
    assert has_vendor_permission(owner.user, vendor, VendorAction.ORDERS)
    assert has_vendor_permission(staff.user, vendor, VendorAction.ORDERS)
    assert not has_vendor_permission(staff.user, vendor, VendorAction.BANK_ACCOUNT)
    assert not has_vendor_permission(staff.user, vendor, VendorAction.MEMBERS)


def test_membership_of_one_vendor_grants_nothing_on_another() -> None:
    owner = VendorMemberFactory.create(role=OWNER)
    other_vendor = VendorFactory.create()
    for action in VendorAction:
        assert not has_vendor_permission(owner.user, other_vendor, action)
    assert member_role(owner.user, other_vendor) is None


def test_non_members_and_platform_staff_get_no_vendor_permission() -> None:
    vendor = VendorMemberFactory.create(role=OWNER).vendor
    # Platform staff reach vendor data through the staff admin (CORE.3/CORE.8), not as members.
    for user in (UserFactory.create(), UserFactory.create(staff=True)):
        assert not has_vendor_permission(user, vendor, VendorAction.ORDERS)


def test_inactive_users_get_no_vendor_permission() -> None:
    owner = VendorMemberFactory.create(role=OWNER, user__is_active=False)
    assert not has_vendor_permission(owner.user, owner.vendor, VendorAction.ORDERS)


def test_permission_check_refuses_an_unknown_action() -> None:
    owner = VendorMemberFactory.create(role=OWNER)
    with pytest.raises(UnknownAction):
        has_vendor_permission(owner.user, owner.vendor, "delete_vendor")
