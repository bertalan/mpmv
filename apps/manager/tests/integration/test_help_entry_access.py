"""ARC.32 / CORE.18: help entries are edited by platform staff, by the role-permission matrix.

The `HelpEntry` snippet is content owned by the "Platform: moderation (... content)" row: the
Catalog and moderation role edits, the platform owner reads, no other role has access and the
technical superuser (break-glass) holds none without a role. The check asks `roles.py` through
role membership, never `has_perm`.
"""

import pytest
from django.contrib.auth.models import Group
from django.test import Client

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.cms.models import HelpEntry
from apps.manager import roles
from apps.manager.wagtail_hooks import HELP_SECTION, HelpEntryPermissionPolicy

pytestmark = pytest.mark.django_db

POLICY = HelpEntryPermissionPolicy(HelpEntry)
EDIT_ACTIONS = ("add", "change", "delete")


def _with_role(key: str, **kwargs: object) -> User:
    user = UserFactory.create(staff=True, **kwargs)
    user.groups.add(Group.objects.get(name=roles.role(key).group))
    return User.objects.get(pk=user.pk)


def test_the_section_is_a_row_of_the_matrix() -> None:
    assert HELP_SECTION in roles.MATRIX


@pytest.mark.parametrize("key", roles.ROLE_KEYS)
def test_every_role_gets_what_the_matrix_row_gives(key: str) -> None:
    user = _with_role(key)
    edits = roles.allows(HELP_SECTION, [key], write=True)
    reads = roles.allows(HELP_SECTION, [key])
    for action in EDIT_ACTIONS:
        assert POLICY.user_has_permission(user, action) is edits, (key, action)
    assert POLICY.user_has_permission(user, "view") is reads
    assert POLICY.user_has_any_permission(user, EDIT_ACTIONS) is edits


def test_the_catalog_role_edits_and_the_owner_role_only_reads() -> None:
    assert roles.allows(HELP_SECTION, ["catalog"], write=True)
    assert roles.allows(HELP_SECTION, ["owner"])
    assert not roles.allows(HELP_SECTION, ["owner"], write=True)


def test_a_superuser_without_a_role_has_no_access() -> None:
    superuser = UserFactory.create(superuser=True)
    assert not POLICY.user_has_permission(superuser, "change")
    assert not POLICY.user_has_permission(superuser, "view")


def test_a_customer_or_an_anonymous_user_has_no_access() -> None:
    from django.contrib.auth.models import AnonymousUser

    assert not POLICY.user_has_permission(UserFactory.create(), "change")
    assert not POLICY.user_has_permission(AnonymousUser(), "view")


def test_a_model_permission_alone_does_not_open_the_snippet() -> None:
    user = UserFactory.create(staff=True, permissions=["cms.change_helpentry", "cms.add_helpentry"])
    assert not POLICY.user_has_permission(user, "change")


def test_the_instance_checks_follow_the_same_rule() -> None:
    entry = HelpEntry.objects.get(kind="general", locale__language_code="en")
    catalog, support = _with_role("catalog"), _with_role("support")
    assert POLICY.user_has_permission_for_instance(catalog, "change", entry)
    assert not POLICY.user_has_permission_for_instance(support, "change", entry)
    editors = POLICY.users_with_permission("change")
    assert catalog in editors
    assert support not in editors


ADMIN_ACCESS = "wagtailadmin.access_admin"
ACCESS_ROLES = ("catalog", "owner")  # lane-a, #86: the roles that edit or read the help texts


def test_the_roles_of_the_help_section_hold_the_editors_admin_access() -> None:
    for key in roles.ROLE_KEYS:
        assert (ADMIN_ACCESS in roles.role(key).permissions) is (key in ACCESS_ROLES), key


def test_the_data_migration_adds_the_access_to_an_existing_database() -> None:
    """A database seeded before this change: the migration's sync adds it, touches nothing else."""
    import importlib

    from django.apps import apps
    from django.contrib.auth.models import Permission

    from apps.manager.role_groups import role_drift

    access = Permission.objects.get(content_type__app_label="wagtailadmin", codename="access_admin")
    group = Group.objects.get(name=roles.role("catalog").group)
    group.permissions.remove(access)
    assert role_drift()  # the drift check sees the missing permission
    migration = importlib.import_module("apps.manager.migrations.0003_role_groups_admin_access")
    migration.sync(apps, None)
    assert role_drift() == []
    assert access in group.permissions.all()


@pytest.mark.parametrize("key", ACCESS_ROLES)
def test_a_role_with_the_help_section_reaches_the_editors_admin_help(key: str) -> None:
    """Through the role group alone: no permission is added to the user."""
    client = Client()
    verified_login(client, _with_role(key))
    response = client.get("/admin/")
    assert response.status_code == 200
    assert "data-admin-help-open" in response.content.decode()
    assert client.get("/admin/snippets/cms/helpentry/").status_code == 200


@pytest.mark.parametrize("key", [k for k in roles.ROLE_KEYS if k not in ACCESS_ROLES])
def test_the_other_roles_do_not_reach_the_editors_admin(key: str) -> None:
    client = Client()
    verified_login(client, _with_role(key))
    for path in ("/admin/", "/admin/snippets/cms/helpentry/"):
        response = client.get(path)
        assert response.status_code != 200, (key, path)
        assert "data-admin-help-open" not in response.content.decode()


def test_the_snippet_list_is_refused_to_a_role_the_matrix_denies_even_with_admin_access() -> None:
    user = _with_role("support", permissions=[ADMIN_ACCESS])
    client = Client()
    verified_login(client, user)
    assert client.get("/admin/snippets/cms/helpentry/").status_code != 200
