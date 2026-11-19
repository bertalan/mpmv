"""CMS.14 follow-up: a snippet is published only with its active review workflow, on every path
(shell, API, scheduled job), not just in the admin. The check and message are shared."""

from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.utils import timezone
from wagtail.models import Workflow

from apps.cms.models import ContentSnippet, content_snippet_workflow
from apps.cms.services import SeedReport
from apps.cms.snippet_workflow import SnippetPublishRefused, snippet_can_publish
from apps.cms.tests.factories import ContentSnippetFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _review_workflow(seeded: SeedReport) -> None:
    content_snippet_workflow(create=True)


def _draft(snippet: ContentSnippet, body: str) -> object:
    snippet.body = body
    return snippet.save_revision()


def _remove() -> None:
    Workflow.objects.filter(name="Content snippet review").delete()


def _deactivate() -> None:
    Workflow.objects.filter(name="Content snippet review").update(active=False)


def test_the_predicate_follows_the_workflow() -> None:
    snippet = ContentSnippetFactory.create()
    assert snippet_can_publish(snippet)
    _deactivate()
    assert not snippet_can_publish(snippet)
    _remove()
    assert not snippet_can_publish(snippet)


def test_shell_publish_is_allowed_with_an_active_workflow() -> None:
    snippet = ContentSnippetFactory.create(body="<p>Old.</p>")
    _draft(snippet, "<p>New.</p>").publish()  # type: ignore[attr-defined]
    snippet.refresh_from_db()
    assert "New." in snippet.body


@pytest.mark.parametrize("break_workflow", [_remove, _deactivate])
def test_shell_publish_is_refused_without_an_active_workflow(break_workflow) -> None:  # type: ignore[no-untyped-def]
    snippet = ContentSnippetFactory.create(body="<p>Old.</p>")
    revision = _draft(snippet, "<p>New.</p>")
    break_workflow()
    with pytest.raises(SnippetPublishRefused, match="cannot be published"):
        revision.publish()  # type: ignore[attr-defined]
    snippet.refresh_from_db()
    assert "Old." in snippet.body


def test_the_refusal_is_a_permission_denied() -> None:
    snippet = ContentSnippetFactory.create()
    revision = _draft(snippet, "<p>New.</p>")
    _remove()
    with pytest.raises(PermissionDenied):
        revision.publish(skip_permission_checks=True)  # type: ignore[attr-defined]


@pytest.mark.parametrize("break_workflow", [_remove, _deactivate])
def test_the_scheduled_job_skips_and_keeps_the_revision_scheduled(break_workflow) -> None:  # type: ignore[no-untyped-def]
    snippet = ContentSnippetFactory.create(body="<p>Old.</p>")
    revision = _draft(snippet, "<p>New.</p>")
    revision.approved_go_live_at = timezone.now() - timedelta(minutes=5)  # type: ignore[attr-defined]
    revision.save(update_fields=["approved_go_live_at"])  # type: ignore[attr-defined]
    break_workflow()
    call_command("publish_scheduled")  # does not crash
    snippet.refresh_from_db()
    revision.refresh_from_db()  # type: ignore[attr-defined]
    assert "Old." in snippet.body
    assert revision.approved_go_live_at is not None  # type: ignore[attr-defined]


def test_the_scheduled_job_publishes_with_an_active_workflow() -> None:
    snippet = ContentSnippetFactory.create(body="<p>Old.</p>")
    revision = _draft(snippet, "<p>New.</p>")
    revision.approved_go_live_at = timezone.now() - timedelta(minutes=5)  # type: ignore[attr-defined]
    revision.save(update_fields=["approved_go_live_at"])  # type: ignore[attr-defined]
    call_command("publish_scheduled")
    snippet.refresh_from_db()
    assert "New." in snippet.body
