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
    assert POLICY.user_has_permission_for_instance(_with_role("catalog"), "change", entry)
    assert not POLICY.user_has_permission_for_instance(_with_role("support"), "change", entry)
    editors = POLICY.users_with_permission("change")
    assert _with_role("catalog") in editors
    assert _with_role("finance") not in editors


def test_the_snippet_list_opens_for_the_catalog_role_in_the_editors_admin() -> None:
    user = _with_role("catalog", permissions=["wagtailadmin.access_admin"])
    client = Client()
    verified_login(client, user)
    assert client.get("/admin/snippets/cms/helpentry/").status_code == 200


def test_the_snippet_list_is_refused_to_another_role_in_the_editors_admin() -> None:
    user = _with_role("support", permissions=["wagtailadmin.access_admin"])
    client = Client()
    verified_login(client, user)
    assert client.get("/admin/snippets/cms/helpentry/").status_code != 200
