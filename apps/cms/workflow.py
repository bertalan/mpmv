"""Nobody approves their own submission (CORE.8, lane-a decision).

Wagtail's `GroupApprovalTask` lets any member of its groups, and every superuser, approve a
page they submitted themselves, which turns "submit for moderation" into a formality. This
withdraws the approve action for the person who requested the workflow, in the three places
Wagtail asks: the actions offered in the editor, the action itself (so a forged POST fails) and
the moderator's dashboard list. Rejecting ("request changes") stays allowed.

Wagtail has no hook for this, so the task class is wrapped once from `CmsConfig.ready`, like
`wagtail_hooks._withdraw` withdraws registrations.
"""

from typing import Any

from django.core.exceptions import PermissionDenied
from wagtail.models import GroupApprovalTask

_WRAPPED = "_cms_no_self_approval"


def _requested_by(user: Any, requester_id: int | None) -> bool:
    return requester_id is not None and getattr(user, "pk", None) == requester_id


def install() -> None:
    if getattr(GroupApprovalTask, _WRAPPED, False):
        return
    on_action = GroupApprovalTask.on_action
    get_actions = GroupApprovalTask.get_actions
    moderatable = GroupApprovalTask.get_task_states_user_can_moderate

    def guarded_on_action(
        self: Any, task_state: Any, user: Any, action_name: str, **kwargs: Any
    ) -> Any:
        if action_name == "approve" and _requested_by(
            user, task_state.workflow_state.requested_by_id
        ):
            raise PermissionDenied("Nobody approves their own submission.")
        return on_action(self, task_state, user, action_name, **kwargs)

    def guarded_get_actions(self: Any, obj: Any, user: Any) -> Any:
        actions = get_actions(self, obj, user)
        state = getattr(obj, "current_workflow_state", None)
        if state is not None and _requested_by(user, state.requested_by_id):
            return [action for action in actions if action[0] != "approve"]
        return actions

    def guarded_moderatable(self: Any, user: Any, **kwargs: Any) -> Any:
        states = moderatable(self, user, **kwargs)
        return states.exclude(workflow_state__requested_by=user) if user.pk else states.none()

    GroupApprovalTask.on_action = guarded_on_action
    GroupApprovalTask.get_actions = guarded_get_actions
    GroupApprovalTask.get_task_states_user_can_moderate = guarded_moderatable
    setattr(GroupApprovalTask, _WRAPPED, True)
