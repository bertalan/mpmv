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
    assert moderator_groups == set(roles.CONTENT_ROLES) | {roles.EVENT_MODERATORS}

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
