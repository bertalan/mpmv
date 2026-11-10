"""CORE.8 part 3/3: the data migration removes Wagtail's default Editors and Moderators groups.

Users in the default groups move to the successor roles (`roles.DEFAULT_GROUP_SUCCESSORS`) so no
editor loses access silently; the step is idempotent and reversible (the reverse recreates the
default groups and puts the content-role members back in "Editors").
"""

import importlib
import secrets

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from apps.cms import roles

BEFORE = [("cms", "0011_blog")]
CODE = "0012_editor_roles"


def _names(model: type, **filters: object) -> set[str]:
    return set(model.objects.filter(**filters).values_list("name", flat=True))  # type: ignore[attr-defined]


@pytest.mark.django_db(transaction=True)
def test_default_group_members_move_to_the_successor_roles_and_back() -> None:
    executor = MigrationExecutor(connection)
    executor.migrate(BEFORE)
    old = executor.loader.project_state(BEFORE).apps
    Group = old.get_model("auth", "Group")
    User = old.get_model("accounts", "User")

    editors, _ = Group.objects.get_or_create(name=roles.DEFAULT_EDITORS)
    moderators, _ = Group.objects.get_or_create(name=roles.DEFAULT_MODERATORS)
    event_group, _ = Group.objects.get_or_create(name=roles.EVENT_MODERATORS)
    plain_editor = User.objects.create(email=f"e-{secrets.token_hex(4)}@example.test")
    moderator = User.objects.create(email=f"m-{secrets.token_hex(4)}@example.test")
    plain_editor.groups.add(editors)
    OldTask = old.get_model("wagtailcore", "GroupApprovalTask")
    ContentType = old.get_model("contenttypes", "ContentType")
    task_type, _ = ContentType.objects.get_or_create(
        app_label="wagtailcore", model="groupapprovaltask"
    )
    task = OldTask.objects.create(name="Migration test approval", content_type=task_type)
    task.groups.add(moderators)
    moderator.groups.add(moderators)
    assert event_group.user_set.count() == 0

    executor.loader.build_graph()
    after = [("cms", CODE)]
    executor.migrate(after)
    new = executor.loader.project_state(after).apps
    NewGroup = new.get_model("auth", "Group")
    NewUser = new.get_model("accounts", "User")

    assert not NewGroup.objects.filter(
        name__in=[roles.DEFAULT_EDITORS, roles.DEFAULT_MODERATORS]
    ).exists()
    assert set(roles.ROLE_NAMES) <= _names(NewGroup)
    plain_groups = _names(NewGroup, user=NewUser.objects.get(pk=plain_editor.pk))
    assert plain_groups == set(roles.CONTENT_ROLES)
    moderator_groups = _names(NewGroup, user=NewUser.objects.get(pk=moderator.pk))
    assert moderator_groups == set(roles.CONTENT_ROLES)  # no implicit Event moderators
    # Nobody gains publish or approval rights from the migration: the publisher roles are
    # empty, and only they (never the editor roles) are approvers of the former task.
    for publisher in roles.PUBLISHER_ROLES:
        assert not NewGroup.objects.get(name=publisher).user_set.exists()
    assert not NewGroup.objects.get(name=roles.EVENT_MODERATORS).user_set.exists()
    NewTask = new.get_model("wagtailcore", "GroupApprovalTask")
    assert _names(NewGroup, groupapprovaltask=NewTask.objects.get(pk=task.pk)) == set(
        roles.PUBLISHER_ROLES
    )

    # Idempotent: running the forward step again changes nothing.
    module = importlib.import_module(f"apps.cms.migrations.{CODE}")
    module.remove_default_groups(new, None)
    assert _names(NewGroup, user=NewUser.objects.get(pk=plain_editor.pk)) == plain_groups

    # Reversible: the default groups return, the content-role members go back to "Editors".
    executor.loader.build_graph()
    executor.migrate(BEFORE)
    back = executor.loader.project_state(BEFORE).apps
    BackGroup = back.get_model("auth", "Group")
    BackUser = back.get_model("accounts", "User")
    assert {roles.DEFAULT_EDITORS, roles.DEFAULT_MODERATORS} <= _names(BackGroup)
    assert roles.DEFAULT_EDITORS in _names(BackGroup, user=BackUser.objects.get(pk=plain_editor.pk))

    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())
    assert not _names(NewGroup, name__in=[roles.DEFAULT_EDITORS, roles.DEFAULT_MODERATORS])


@pytest.mark.django_db(transaction=True)
def test_the_migration_runs_on_a_database_without_the_default_groups() -> None:
    executor = MigrationExecutor(connection)
    executor.migrate(BEFORE)
    old = executor.loader.project_state(BEFORE).apps
    old.get_model("auth", "Group").objects.filter(
        name__in=[roles.DEFAULT_EDITORS, roles.DEFAULT_MODERATORS]
    ).delete()
    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())
    new = executor.loader.project_state(executor.loader.graph.leaf_nodes()).apps
    assert set(roles.ROLE_NAMES) <= _names(new.get_model("auth", "Group"))


@pytest.mark.django_db(transaction=True)
def test_the_reverse_does_not_widen_a_single_role_member_to_editors() -> None:
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())
    leaf = executor.loader.graph.leaf_nodes()
    apps = executor.loader.project_state(leaf).apps
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("accounts", "User")
    blog_only = User.objects.create(email=f"b-{secrets.token_hex(4)}@example.test")
    all_three = User.objects.create(email=f"a-{secrets.token_hex(4)}@example.test")
    for name in roles.CONTENT_ROLES:
        Group.objects.get_or_create(name=name)[0].user_set.add(all_three)
    Group.objects.get_or_create(name=roles.BLOG_EDITORS)[0].user_set.add(blog_only)

    executor.migrate(BEFORE)
    back = executor.loader.project_state(BEFORE).apps
    BackGroup = back.get_model("auth", "Group")
    BackUser = back.get_model("accounts", "User")
    assert roles.DEFAULT_EDITORS in _names(BackGroup, user=BackUser.objects.get(pk=all_three.pk))
    assert roles.DEFAULT_EDITORS not in _names(
        BackGroup, user=BackUser.objects.get(pk=blog_only.pk)
    )
    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())
