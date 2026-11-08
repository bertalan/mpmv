"""EVT.1: the denormalised `status` follows the Wagtail state (spec 15 "Status").

`live`, the workflow state, `cancelled_at` and `hidden_at` are the source of truth; one function
(`services.sync_event_status`) writes `status`, called from the services and from Wagtail's
publish, unpublish and workflow signals.
"""

import pytest
from django.utils import timezone
from wagtail.models import GroupApprovalTask, Workflow, WorkflowPage

from apps.accounts.tests.factories import UserFactory
from apps.core.changeset import ChangeSet
from apps.events.models import EventPage
from apps.events.services import sync_event_status
from apps.events.tests.factories import EventPageFactory

pytestmark = pytest.mark.django_db


def _status(page: EventPage) -> str:
    return str(EventPage.objects.get(pk=page.pk).status)


def test_a_new_page_is_a_draft() -> None:
    page = EventPageFactory.create()
    assert _status(page) == EventPage.Status.DRAFT


def test_publishing_and_unpublishing_sync_the_status() -> None:
    page = EventPageFactory.create()
    page.save_revision().publish()
    assert _status(page) == EventPage.Status.PUBLISHED
    page.refresh_from_db()
    page.unpublish()
    assert _status(page) == EventPage.Status.DRAFT


def test_a_cancelled_live_event_is_cancelled() -> None:
    page = EventPageFactory.create()
    page.cancelled_at = timezone.now()
    page.save_revision().publish()
    assert _status(page) == EventPage.Status.CANCELLED


def test_a_hidden_event_is_hidden() -> None:
    page = EventPageFactory.create()
    page.save_revision().publish()
    page.refresh_from_db()
    page.hidden_at = timezone.now()
    page.hidden_reason = "Notice received"
    page.save(update_fields=["hidden_at", "hidden_reason"])
    page.unpublish()
    assert _status(page) == EventPage.Status.HIDDEN


def test_a_first_publication_in_review_is_pending() -> None:
    page = EventPageFactory.create()
    workflow = Workflow.objects.create(name="Event review")
    task = GroupApprovalTask.objects.create(name="Review")
    workflow.workflow_tasks.create(task=task, sort_order=0)
    WorkflowPage.objects.create(workflow=workflow, page=page.get_parent())
    author = UserFactory.create(staff=True)
    page.save_revision(user=author)
    workflow.start(page, author)
    assert _status(page) == EventPage.Status.PENDING


def test_sync_reports_the_change_in_the_callers_changeset() -> None:
    page = EventPageFactory.create()
    EventPage.objects.filter(pk=page.pk).update(status=EventPage.Status.PUBLISHED)
    changes = ChangeSet()
    assert sync_event_status(page, changes) == EventPage.Status.DRAFT
    assert _status(page) == EventPage.Status.DRAFT
    assert changes.summary()["update"] == 1
    assert sync_event_status(page, changes) == EventPage.Status.DRAFT
    assert changes.summary()["update"] == 1  # nothing changed the second time
