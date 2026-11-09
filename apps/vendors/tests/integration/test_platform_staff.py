"""CORE.8 (part 1): platform staff is a dedicated permission, not `is_staff` (spec 02, Q-34).

An editor or copywriter with Wagtail admin access (`is_staff`) must see no vendor's data.
"""

import pytest
from django.contrib.auth.models import Group, Permission
from django.http import Http404

from apps.accounts.tests.factories import UserFactory
from apps.vendors.models import Vendor, VendorMember
from apps.vendors.scoping import (
    PLATFORM_STAFF_PERMISSION,
    get_for_actor_or_404,
    is_platform_staff,
)
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def two_vendors() -> tuple[VendorMember, VendorMember]:
    return (
        VendorMemberFactory.create(vendor=VendorFactory.create()),
        VendorMemberFactory.create(vendor=VendorFactory.create()),
    )


def test_the_permission_exists_on_the_vendor_model() -> None:
    assert PLATFORM_STAFF_PERMISSION == "vendors.view_all_vendors"
    assert Permission.objects.filter(
        content_type__app_label="vendors", codename="view_all_vendors"
    ).exists()


def test_is_staff_alone_is_not_platform_staff() -> None:
    assert not is_platform_staff(UserFactory.create(staff=True))


def test_the_permission_without_is_staff_is_platform_staff() -> None:
    # The Manager does not need Wagtail access: the permission alone defines platform staff.
    assert is_platform_staff(UserFactory.create(permissions=[PLATFORM_STAFF_PERMISSION]))


def test_platform_staff_via_a_role_group() -> None:
    group = Group.objects.create(name="role-test-support")
    group.permissions.add(Permission.objects.get(codename="view_all_vendors"))
    user = UserFactory.create(staff=True)
    user.groups.add(group)
    assert is_platform_staff(user)


def test_anonymous_and_inactive_are_never_platform_staff() -> None:
    from django.contrib.auth.models import AnonymousUser

    assert not is_platform_staff(AnonymousUser())
    assert not is_platform_staff(
        UserFactory.create(is_active=False, permissions=[PLATFORM_STAFF_PERMISSION])
    )


# --- Negative: Wagtail admin access sees no vendor's data


def test_wagtail_editor_sees_no_vendor_rows(
    two_vendors: tuple[VendorMember, VendorMember],
) -> None:
    editor = UserFactory.create(staff=True)
    assert not Vendor.objects.for_actor(editor).exists()
    assert not VendorMember.objects.for_actor(editor).exists()


def test_wagtail_editor_gets_404_for_any_vendor(
    two_vendors: tuple[VendorMember, VendorMember],
) -> None:
    owner_a, _ = two_vendors
    editor = UserFactory.create(staff=True)
    with pytest.raises(Http404):
        get_for_actor_or_404(Vendor, editor, pk=owner_a.vendor.pk)


def test_vendor_staff_never_sees_another_vendors_rows(
    two_vendors: tuple[VendorMember, VendorMember],
) -> None:
    owner_a, owner_b = two_vendors
    staff_a = VendorMemberFactory.create(vendor=owner_a.vendor, role=VendorMember.Role.STAFF)
    assert list(Vendor.objects.for_actor(staff_a.user)) == [owner_a.vendor]
    assert set(VendorMember.objects.for_actor(staff_a.user)) == {owner_a, staff_a}
    with pytest.raises(Http404):
        get_for_actor_or_404(Vendor, staff_a.user, pk=owner_b.vendor.pk)


def test_a_vendor_member_with_is_staff_still_sees_only_their_vendor(
    two_vendors: tuple[VendorMember, VendorMember],
) -> None:
    owner_a, _ = two_vendors
    owner_a.user.is_staff = True
    owner_a.user.save(update_fields=["is_staff"])
    assert list(Vendor.objects.for_actor(owner_a.user)) == [owner_a.vendor]


# --- Positive: the permission opens every vendor


def test_platform_staff_see_every_vendor(
    two_vendors: tuple[VendorMember, VendorMember],
) -> None:
    owner_a, owner_b = two_vendors
    staff = UserFactory.create(platform_staff=True)
    assert set(Vendor.objects.for_actor(staff)) == {owner_a.vendor, owner_b.vendor}
    assert get_for_actor_or_404(Vendor, staff, pk=owner_b.vendor.pk) == owner_b.vendor
