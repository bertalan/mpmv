"""CORE.8: the self-approval guard wraps Wagtail's task once and keeps its method signatures."""

import inspect

import pytest
from wagtail.models import GroupApprovalTask

from apps.cms import workflow

pytestmark = pytest.mark.django_db

PATCHED = ("on_action", "get_actions", "get_task_states_user_can_moderate")


def test_install_is_idempotent() -> None:
    workflow.install()
    before = {name: getattr(GroupApprovalTask, name) for name in PATCHED}
    workflow.install()
    workflow.install()
    assert {name: getattr(GroupApprovalTask, name) for name in PATCHED} == before


def test_the_patched_methods_keep_wagtails_signatures() -> None:
    """If Wagtail changes a signature, this fails instead of the guard silently misfiring."""
    expected = {
        "on_action": ["self", "task_state", "user", "action_name", "kwargs"],
        "get_actions": ["self", "obj", "user"],
        "get_task_states_user_can_moderate": ["self", "user", "kwargs"],
    }
    for name, params in expected.items():
        assert list(inspect.signature(getattr(GroupApprovalTask, name)).parameters) == params
