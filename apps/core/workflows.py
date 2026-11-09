"""Wagtail workflow glue shared by every moderated model (EVT.3, spec 15).

`WAGTAIL_FINISH_WORKFLOW_ACTION` points here. Wagtail's default finish action publishes the
object's latest revision, so a revision saved while the review was in progress would go live on
approval without anyone reviewing it. This action publishes the revision the reviewer saw.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # wagtail.models imports this module while it loads (`WorkflowState.on_finish`)
    from wagtail.models import Revision, WorkflowState


def publish_reviewed_revision(workflow_state: WorkflowState, user: Any = None) -> None:
    """Publish the revision under review when a workflow is approved.

    The reviewed revision is the one the final task state was started on. The latest revision
    replaces it only when the approver saved it (Wagtail's "approve with edits"): the approver
    reviewed their own changes. Any other later revision stays a draft.
    """
    latest: Revision | None = workflow_state.content_object.get_latest_revision()
    task_state = workflow_state.current_task_state
    reviewed: Revision | None = task_state.revision if task_state is not None else None
    revision = reviewed
    if latest is not None and (
        reviewed is None
        or latest.pk == reviewed.pk
        or (user is not None and user.pk is not None and latest.user_id == user.pk)
    ):
        revision = latest
    if revision is not None:
        revision.publish(user=user)
