"""CMS.14 follow-up: the snippet review workflow fails closed, and deleting a used snippet warns.

Without the "Content snippet review" workflow (missing or inactive) an edit would publish with
no review, so publishing is refused with a message and `manage.py check` fails (cms.E005).
"""

import json

import pytest
from bs4 import BeautifulSoup
from django.test import Client
from django.urls import reverse
from wagtail.models import Workflow

from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.cms.checks import check_snippet_workflow
from apps.cms.models import ContentSnippet, content_snippet_workflow
from apps.cms.services import SeedReport
from apps.cms.tests.factories import ContentSnippetFactory, StandardPageFactory
from tests.i18n import translated

pytestmark = pytest.mark.django_db

REFUSED = "cannot be published"


@pytest.fixture(autouse=True)
def _review_workflow(seeded: SeedReport) -> None:
    content_snippet_workflow(create=True)


def _admin() -> Client:
    client = Client()
    verified_login(client, UserFactory.create(is_staff=True, is_superuser=True))
    return client


def _draftail(text: str) -> str:
    """The rich text editor posts its content as Draft.js JSON."""
    block = {
        "key": "a1",
        "text": text,
        "type": "unstyled",
        "depth": 0,
        "inlineStyleRanges": [],
        "entityRanges": [],
    }
    return json.dumps({"blocks": [block], "entityMap": {}})


def _publish(client: Client, snippet: ContentSnippet, body: str) -> tuple[int, str]:
    url = reverse("wagtailsnippets_cms_contentsnippet:edit", args=[snippet.pk])
    response = client.post(
        url,
        {"title": snippet.title, "body": _draftail(body), "action-publish": "Publish"},
        follow=True,
    )
    return response.status_code, response.content.decode()


def _remove_workflow() -> None:
    Workflow.objects.filter(name="Content snippet review").delete()


def _deactivate_workflow() -> None:
    Workflow.objects.filter(name="Content snippet review").update(active=False)


def test_publishing_is_allowed_while_the_workflow_is_active() -> None:
    snippet = ContentSnippetFactory.create(body="<p>Old.</p>")
    status, html = _publish(_admin(), snippet, "New.")
    assert status == 200
    assert REFUSED not in html
    snippet.refresh_from_db()
    assert "New." in snippet.body


def test_publishing_is_refused_with_a_message_when_the_workflow_is_missing() -> None:
    snippet = ContentSnippetFactory.create(body="<p>Old.</p>")
    _remove_workflow()
    status, html = _publish(_admin(), snippet, "New.")
    assert status == 200
    assert REFUSED in html
    snippet.refresh_from_db()
    assert "Old." in snippet.body


def test_publishing_is_refused_when_the_workflow_is_inactive() -> None:
    snippet = ContentSnippetFactory.create(body="<p>Old.</p>")
    _deactivate_workflow()
    status, html = _publish(_admin(), snippet, "New.")
    assert status == 200
    assert REFUSED in html
    snippet.refresh_from_db()
    assert "Old." in snippet.body


def test_the_check_passes_with_an_active_workflow() -> None:
    assert check_snippet_workflow(None) == []


def test_the_check_fails_when_the_workflow_is_missing() -> None:
    _remove_workflow()
    assert [e.id for e in check_snippet_workflow(None)] == ["cms.E005"]


def test_the_check_fails_when_the_workflow_is_inactive() -> None:
    _deactivate_workflow()
    assert [e.id for e in check_snippet_workflow(None)] == ["cms.E005"]


def test_the_check_recovers_once_the_workflow_is_recreated() -> None:
    _remove_workflow()
    content_snippet_workflow(create=True)
    assert check_snippet_workflow(None) == []


def _delete_page(snippet: ContentSnippet) -> str:
    url = reverse("wagtailsnippets_cms_contentsnippet:delete", args=[snippet.pk])
    response = _admin().get(url)
    assert response.status_code == 200
    return response.content.decode()


def test_deleting_a_used_snippet_warns_with_the_page_count() -> None:
    snippet = ContentSnippetFactory.create()
    for title in ("One", "Two"):
        page = StandardPageFactory.create(
            title=title, body=[("content_snippet", {"snippet": snippet})]
        )
        page.save_revision().publish()
    html = _delete_page(snippet)
    assert "still used by 2 pages" in html
    assert "stop showing this text" in html


def test_deleting_an_unused_snippet_shows_no_warning() -> None:
    html = _delete_page(ContentSnippetFactory.create())
    assert BeautifulSoup(html, "html.parser").select(".help-warning") == []
    assert translated("Yes, delete") in html
