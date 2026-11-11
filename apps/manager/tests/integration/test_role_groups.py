"""CORE.18 (part 1) done-when: seeded groups, idempotence, drift check, role membership."""

import pytest
from django.apps import apps
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType

from apps.accounts.tests.factories import UserFactory
from apps.manager import roles
from apps.manager.access import is_platform_staff, may_enter
from apps.manager.checks import check_role_drift, check_roles_matrix
from apps.manager.role_groups import role_drift, sync_role_groups, user_roles
from apps.manager.spaces import Space
from apps.vendors.tests.factories import VendorMemberFactory

pytestmark = pytest.mark.django_db


def _perms(group: Group) -> set[str]:
    return {
        f"{a}.{c}" for a, c in group.permissions.values_list("content_type__app_label", "codename")
    }


def _sync() -> list[str]:
    return sync_role_groups(Group, Permission, ContentType, apps.get_model)


@pytest.mark.parametrize("role", roles.ROLES, ids=lambda r: r.key)
def test_the_migration_seeded_each_group_with_exactly_its_permissions(role: roles.Role) -> None:
    assert _perms(Group.objects.get(name=role.group)) == set(role.permissions)


def test_nobody_is_in_a_role_group_after_seeding() -> None:
    assert not Group.objects.filter(name__in=roles.group_names(), user__isnull=False).exists()


def test_seeding_twice_changes_nothing() -> None:
    before = {g.pk: _perms(g) for g in Group.objects.all()}
    assert _sync() == []
    assert _sync() == []
    assert {g.pk: _perms(g) for g in Group.objects.all()} == before


def test_seeding_repairs_a_missing_group_and_keeps_members() -> None:
    user = UserFactory.create(staff=True)
    group = Group.objects.get(name=roles.role("support").group)
    user.groups.add(group)
    group.permissions.clear()
    assert _sync() == [group.name]
    assert user.groups.filter(pk=group.pk).exists()
    assert _perms(group) == set(roles.role("support").permissions)


def test_no_drift_and_clean_checks_on_a_seeded_database() -> None:
    assert role_drift() == []
    assert check_role_drift() == []
    assert check_roles_matrix() == []


def test_an_admin_edit_of_a_role_group_fails_the_check() -> None:
    group = Group.objects.get(name=roles.role("catalog").group)
    group.permissions.add(Permission.objects.get(codename="platform_finance"))
    errors = check_role_drift()
    assert [e.id for e in errors] == ["manager.E004"]
    assert "platform_finance" in errors[0].msg


def test_a_removed_permission_and_a_deleted_group_fail_the_check() -> None:
    Group.objects.get(name=roles.role("finance").group).permissions.clear()
    Group.objects.filter(name=roles.role("config").group).delete()
    assert len(check_role_drift()) == 2


def test_a_member_of_a_role_group_is_platform_staff_with_that_role() -> None:
    user = UserFactory.create(staff=True)
    user.groups.add(Group.objects.get(name=roles.role("support").group))
    assert user_roles(user) == {"support"}
    assert is_platform_staff(user)
    assert may_enter(Space.PLATFORM, user)


def test_the_owner_group_makes_a_platform_owner() -> None:
    from apps.core.services import is_platform_owner

    user = UserFactory.create()
    user.groups.add(Group.objects.get(name=roles.role("owner").group))
    assert is_platform_owner(user)


def test_no_other_role_group_makes_a_platform_owner() -> None:
    from apps.core.services import is_platform_owner

    for key in ("config", "catalog", "finance", "support"):
        user = UserFactory.create()
        user.groups.add(Group.objects.get(name=roles.role(key).group))
        assert not is_platform_owner(user)


def test_a_superuser_or_staff_without_a_role_is_not_platform_staff() -> None:
    assert not is_platform_staff(UserFactory.create(is_superuser=True, is_staff=True))
    assert not is_platform_staff(UserFactory.create(is_staff=True))


def test_an_inactive_role_holder_has_no_role() -> None:
    user = UserFactory.create(is_active=False)
    user.groups.add(Group.objects.get(name=roles.role("support").group))
    assert user_roles(user) == frozenset()
    assert not is_platform_staff(user)


def test_a_role_holder_who_is_a_vendor_member_enters_neither_space() -> None:
    member = VendorMemberFactory.create().user
    member.groups.add(Group.objects.get(name=roles.role("catalog").group))
    assert not may_enter(Space.PLATFORM, member)
    assert not may_enter(Space.SHOP, member)


def test_before_the_seed_migration_missing_groups_do_not_block_migrate() -> None:
    """`migrate` runs the database checks first: on a database that predates the seed migration the
    groups are missing by design, and the check must let the migration that creates them run."""
    from django.db.migrations.recorder import MigrationRecorder

    from apps.manager.checks import SEED_MIGRATION

    app, name = SEED_MIGRATION
    MigrationRecorder.Migration.objects.filter(app=app, name=name).delete()
    Group.objects.filter(name__in=[r.group for r in roles.ROLES]).delete()
    assert check_role_drift() == []
