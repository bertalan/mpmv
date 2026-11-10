"""CORE.8 part 3/3 (lane-a decision): editing and publishing are separate roles.

The editor roles never publish, so their work is moderated; the publisher roles (assigned by
staff only, the migration puts nobody there) publish and approve within their own subtree; and
nobody approves a submission they requested themselves.
"""

from typing import Any

import pytest
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.urls import reverse
from wagtail.models import GroupApprovalTask, Page, Workflow, WorkflowPage, WorkflowTask

from apps.accounts.models import User
from apps.accounts.tests.mfa import verified_login
from apps.cms import roles
from apps.cms.models import BlogIndexPage
from apps.cms.tests.factories import (
    BlogPostPageFactory,
    LegalPageFactory,
    StandardPageFactory,
    editor_user,
)

pytestmark = pytest.mark.django_db

EDIT_ONLY = (roles.PAGE_EDITORS, roles.BLOG_EDITORS, roles.LEGAL_EDITORS)
PUBLISHER_FOR = {
    roles.PAGE_PUBLISHERS: "about",
    roles.BLOG_PUBLISHERS: "post",
    roles.LEGAL_PUBLISHERS: "legal",
}


def _fresh(user: Any) -> User:
    return User.objects.get(pk=user.pk)


@pytest.fixture
def tree() -> dict[str, Page]:
    post = BlogPostPageFactory.create()
    return {
        "about": StandardPageFactory.create(title="About us"),
        "legal": LegalPageFactory.create(),
        "post": post,
        "blog": BlogIndexPage.objects.get(pk=post.get_parent().pk),
    }


@pytest.mark.parametrize("role", EDIT_ONLY)
def test_the_editor_roles_never_publish_or_unpublish(role: str, tree: dict[str, Page]) -> None:
    user = _fresh(editor_user(role))
    for page in tree.values():
        perms = page.permissions_for_user(user)
        assert not perms.can_publish(), page
        assert not perms.can_unpublish(), page


def test_the_editor_roles_still_edit_and_blog_editors_add(tree: dict[str, Page]) -> None:
    assert tree["about"].permissions_for_user(_fresh(editor_user(roles.PAGE_EDITORS))).can_edit()
    assert tree["legal"].permissions_for_user(_fresh(editor_user(roles.LEGAL_EDITORS))).can_edit()
    blog = _fresh(editor_user(roles.BLOG_EDITORS))
    assert tree["blog"].permissions_for_user(blog).can_add_subpage()
    assert tree["post"].permissions_for_user(blog).can_edit()


@pytest.mark.parametrize("role", list(PUBLISHER_FOR))
def test_a_publisher_publishes_only_within_its_subtree(role: str, tree: dict[str, Page]) -> None:
    user = _fresh(editor_user(role))
    own = PUBLISHER_FOR[role]
    assert tree[own].permissions_for_user(user).can_publish()
    for key in set(tree) - {own, "blog" if own == "post" else own}:
        perms = tree[key].permissions_for_user(user)
        assert not perms.can_publish(), key
        assert not perms.can_unpublish(), key


def test_a_publisher_is_a_distinct_group_from_the_editor_of_the_same_part() -> None:
    names = set(roles.ROLE_NAMES)
    assert set(roles.PUBLISHER_ROLES) <= names
    assert not set(roles.PUBLISHER_ROLES) & set(roles.CONTENT_ROLES)
    for role in roles.ROLES:
        has_publish = roles.PUBLISH in role.page_permissions
        assert has_publish == (role.name in (*roles.PUBLISHER_ROLES, roles.EVENT_MODERATORS))


# --- Workflow: nobody approves their own submission


def _workflow(*groups: str) -> tuple[Workflow, GroupApprovalTask]:
    task = GroupApprovalTask.objects.create(name="Test approval")
    task.groups.add(*Group.objects.filter(name__in=groups))
    workflow = Workflow.objects.create(name="Test review")
    WorkflowTask.objects.create(workflow=workflow, task=task, sort_order=0)
    WorkflowPage.objects.filter(page=Page.get_first_root_node()).delete()  # the default one
    WorkflowPage.objects.create(workflow=workflow, page=Page.get_first_root_node())
    return workflow, task


def _submit(page: Page, workflow: Workflow, user: User) -> Any:
    page.save_revision(user=user)
    workflow.start(page, user)
    return page.current_workflow_task_state


def _approve_offered(task: GroupApprovalTask, page: Page, user: User) -> bool:
    return any(action[0] == "approve" for action in task.get_actions(page, _fresh(user)))


def test_a_publisher_cannot_approve_their_own_submission(tree: dict[str, Page]) -> None:
    workflow, task = _workflow(roles.LEGAL_PUBLISHERS)
    author = editor_user(roles.LEGAL_PUBLISHERS)
    colleague = editor_user(roles.LEGAL_PUBLISHERS)
    state = _submit(tree["legal"], workflow, author)
    assert not _approve_offered(task, tree["legal"], author)
    assert _approve_offered(task, tree["legal"], colleague)
    with pytest.raises(PermissionDenied):
        task.on_action(state, _fresh(author), "approve")
    state.refresh_from_db()
    assert state.status == state.STATUS_IN_PROGRESS
    assert task.get_task_states_user_can_moderate(_fresh(author)).count() == 0
    assert task.get_task_states_user_can_moderate(_fresh(colleague)).count() == 1


def test_a_superuser_cannot_approve_their_own_submission_either(tree: dict[str, Page]) -> None:
    from apps.accounts.tests.factories import UserFactory

    workflow, task = _workflow(roles.LEGAL_PUBLISHERS)
    root: User = UserFactory.create(superuser=True)
    state = _submit(tree["legal"], workflow, root)
    assert not _approve_offered(task, tree["legal"], root)
    with pytest.raises(PermissionDenied):
        task.on_action(state, _fresh(root), "approve")


def test_a_colleague_approves_and_the_author_may_still_request_changes(
    tree: dict[str, Page],
) -> None:
    workflow, task = _workflow(roles.LEGAL_PUBLISHERS)
    author = editor_user(roles.LEGAL_PUBLISHERS)
    colleague = editor_user(roles.LEGAL_PUBLISHERS)
    state = _submit(tree["legal"], workflow, author)
    assert any(a[0] == "reject" for a in task.get_actions(tree["legal"], _fresh(author)))
    task.on_action(state, _fresh(colleague), "approve")
    state.refresh_from_db()
    assert state.status == state.STATUS_APPROVED


def test_an_editor_is_not_an_approver_of_the_default_task(tree: dict[str, Page]) -> None:
    """The migration adds the publisher roles to the default task, never the editor roles."""
    workflow, task = _workflow(*roles.PUBLISHER_ROLES)
    author = editor_user(roles.LEGAL_EDITORS)
    state = _submit(tree["legal"], workflow, author)
    assert not task.get_actions(tree["legal"], _fresh(author))
    assert not _approve_offered(task, tree["legal"], editor_user(roles.BLOG_EDITORS))
    assert state.task.specific.groups.filter(name__in=roles.CONTENT_ROLES).count() == 0


def test_a_forged_approve_post_through_the_workflow_view_is_refused(
    client: Client, tree: dict[str, Page]
) -> None:
    workflow, _task = _workflow(roles.LEGAL_PUBLISHERS)
    author = editor_user(roles.LEGAL_PUBLISHERS)
    colleague = editor_user(roles.LEGAL_PUBLISHERS)
    state = _submit(tree["legal"], workflow, author)
    url = reverse(
        "wagtailadmin_pages:workflow_action", args=(tree["legal"].pk, "approve", state.pk)
    )
    verified_login(client, author)
    client.post(url, {"action": "approve"})
    state.refresh_from_db()
    assert state.status == state.STATUS_IN_PROGRESS
    client.logout()
    verified_login(client, colleague)
    client.post(url, {"action": "approve"})
    state.refresh_from_db()
    assert state.status == state.STATUS_APPROVED
