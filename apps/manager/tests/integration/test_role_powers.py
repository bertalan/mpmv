"""CORE.18 security review: role groups never widen existing powers; roles never mix (spec 02).

Unlocking an account is a Support action (and the owner's); duplicating a product to another
vendor is a Catalog and moderation action (and the owner's). A vendor member or a customer in a
role group gets no role powers anywhere.
"""

import pytest
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied

from apps.accounts import services as account_services
from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.catalog.models import Product
from apps.catalog.services import create_product, duplicate_product_to_vendor
from apps.catalog.tests.integration.test_audit6 import attributes, tee  # noqa: F401
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.manager import roles
from apps.manager.access import is_platform_staff as manager_is_platform_staff
from apps.manager.checks import check_role_drift
from apps.manager.role_groups import role_conflicts, sync_role_groups, user_roles
from apps.vendors.models import Vendor, VendorMember
from apps.vendors.scoping import is_platform_staff
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db
CTX = AuditContext(actor_label="staff:test", source=AuditLog.Source.ADMIN)
UNLOCKERS = {"support", "owner"}
DUPLICATORS = {"catalog", "owner"}


def _with_role(key: str) -> User:
    user = UserFactory.create(staff=True)
    user.groups.add(Group.objects.get(name=roles.role(key).group))
    return User.objects.get(pk=user.pk)


def _locked() -> User:
    user = UserFactory.create()
    for _ in range(account_services.ACCOUNT_LOCKOUT.free_attempts + 1):
        account_services.record_failed_login(user.email, "203.0.113.9")
    return user


@pytest.mark.parametrize("key", roles.ROLE_KEYS)
def test_unlock_needs_the_support_or_owner_role(key: str) -> None:
    actor, target = _with_role(key), _locked()
    if key in UNLOCKERS:
        account_services.unlock_account(target, actor=actor, ctx=CTX)
        assert not account_services.login_retry_after(target.email, None)
    else:
        with pytest.raises(PermissionDenied):
            account_services.unlock_account(target, actor=actor, ctx=CTX)
        assert account_services.login_retry_after(target.email, None)


def test_view_all_vendors_alone_no_longer_unlocks() -> None:
    actor = UserFactory.create(staff=True, permissions=["vendors.view_all_vendors"])
    with pytest.raises(PermissionDenied):
        account_services.unlock_account(_locked(), actor=actor, ctx=CTX)


@pytest.mark.parametrize("key", roles.ROLE_KEYS)
def test_duplicate_needs_the_catalog_or_owner_role(key: str, attributes: dict) -> None:  # noqa: F811
    source = VendorFactory.create()
    create_product(source, tee(), ctx=CTX)
    original, target = Product.objects.get(sku="TEE-1"), VendorFactory.create()
    actor = _with_role(key)
    if key in DUPLICATORS:
        duplicate_product_to_vendor(original, target, actor=actor, reason="x", ctx=CTX)
        assert Product.objects.filter(vendor=target).exists()
    else:
        with pytest.raises(PermissionDenied):
            duplicate_product_to_vendor(original, target, actor=actor, reason="x", ctx=CTX)
        assert not Product.objects.filter(vendor=target).exists()


# --- a vendor member or a customer in a role group gets no role powers


def _member_in(key: str) -> User:
    user = VendorMemberFactory.create().user
    user.groups.add(Group.objects.get(name=roles.role(key).group))
    return User.objects.get(pk=user.pk)


@pytest.mark.parametrize("key", roles.ROLE_KEYS)
def test_a_vendor_member_in_a_role_group_is_not_staff_anywhere(key: str) -> None:
    member = _member_in(key)
    assert user_roles(member) == frozenset()
    assert not is_platform_staff(member)
    assert not manager_is_platform_staff(member)


def test_for_actor_gives_such_a_member_only_their_own_vendor() -> None:
    member = _member_in("support")
    other = VendorFactory.create()
    assert list(Vendor.objects.for_actor(member)) == [member_vendor(member)]
    assert other not in Vendor.objects.for_actor(member)


def member_vendor(user: User) -> Vendor:
    return VendorMember.objects.get(user=user).vendor


def test_a_member_in_the_support_group_cannot_unlock() -> None:
    with pytest.raises(PermissionDenied):
        account_services.unlock_account(_locked(), actor=_member_in("support"), ctx=CTX)


def test_a_customer_in_a_role_group_is_not_staff() -> None:
    customer = UserFactory.create()  # not is_staff
    customer.groups.add(Group.objects.get(name=roles.role("finance").group))
    customer = User.objects.get(pk=customer.pk)
    assert user_roles(customer) == frozenset()
    assert not is_platform_staff(customer)
    assert not Vendor.objects.for_actor(customer).exists()


def test_the_check_flags_a_vendor_member_or_customer_in_a_role_group() -> None:
    member, customer = _member_in("catalog"), UserFactory.create()
    customer.groups.add(Group.objects.get(name=roles.role("support").group))
    flagged = role_conflicts()
    assert any(member.email in p for p in flagged)
    assert any(customer.email in p for p in flagged)
    assert {e.id for e in check_role_drift()} == {"manager.E005"}


# --- migration adoption


def test_seeding_refuses_a_pre_existing_group_with_members() -> None:
    from apps.manager.role_groups import refuse_adopting_populated_groups

    name = roles.role("support").group
    Group.objects.filter(name=name).delete()
    group = Group.objects.create(name=name)
    group.user_set.add(UserFactory.create())
    with pytest.raises(RuntimeError, match="already exists and has members"):
        refuse_adopting_populated_groups(Group)


def test_seeding_adopts_a_pre_existing_empty_group() -> None:
    from apps.manager.role_groups import refuse_adopting_populated_groups

    Group.objects.filter(name=roles.role("support").group).delete()
    Group.objects.create(name=roles.role("support").group)
    refuse_adopting_populated_groups(Group)  # no error


def test_seeded_permissions_carry_the_meta_names() -> None:
    from django.apps import apps

    Permission.objects.filter(codename="platform_support").delete()
    sync_role_groups(Group, Permission, ContentType, apps.get_model)
    assert Permission.objects.get(codename="platform_support").name == "Platform role: support"
