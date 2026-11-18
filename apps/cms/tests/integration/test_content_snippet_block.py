"""CMS.14 (D-36): the reusable content snippet and the `content_snippet` block.

One text written once, placed in any page body by a chooser. A live snippet renders in the
page's language (its own translation, else the primary language's, as spec 09's explicit
fallback); an unpublished one renders nothing; editing it reaches every page only when the
edit is published (after the moderation step), and the page-level caches are dropped then.
"""

import re
from types import SimpleNamespace
from typing import Any

import pytest
from django.core.cache import cache
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext, override_settings
from django.urls import reverse
from django.utils import translation
from wagtail.models import Locale, ReferenceIndex, WorkflowState
from wagtail.snippets.blocks import SnippetChooserBlock

from apps.cms import snippet_cache
from apps.cms.blocks import ContentSnippetBlock
from apps.cms.models import BlogPostPage, ContentSnippet, HomePage, StandardPage, body_field
from apps.cms.tests.factories import ContentSnippetFactory, StandardPageFactory
from apps.core.i18n import primary_language
from tests.i18n import other_language

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("english_interface")]


@pytest.fixture(autouse=True)
def _commit_hooks_run_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "django.db.transaction.on_commit", lambda func, using=None, robust=False: func()
    )


def _locale(code: str) -> Locale:
    locale: Locale = Locale.objects.get(language_code=code)
    return locale


def _page_in(code: str) -> Any:
    return SimpleNamespace(locale=_locale(code))


def render(theme: str, snippet: ContentSnippet, language: str | None = None) -> str:
    language = language or primary_language()
    block = ContentSnippetBlock()
    value = block.to_python({"snippet": snippet.pk})
    with override_settings(MPMV_THEME_DEFAULT=theme), translation.override(language):
        return str(block.render(value, context={"page": _page_in(language)})).strip()


def translated(snippet: ContentSnippet, language: str, body: str, *, live: bool) -> ContentSnippet:
    copy: ContentSnippet = snippet.copy_for_translation(_locale(language))
    copy.body = body
    copy.live = live
    copy.save()
    return copy


def publish_edit(snippet: ContentSnippet, body: str) -> None:
    snippet.body = body
    snippet.save_revision().publish()
    snippet.refresh_from_db()


def page_with(snippet: ContentSnippet, **extra: Any) -> StandardPage:
    page: StandardPage = StandardPageFactory.create(
        title="Shipping", body=[("content_snippet", {"snippet": snippet.pk})], **extra
    )
    page.save_revision().publish()
    page.refresh_from_db()
    return page


def body_of(path: str) -> str:
    response = Client().get(path)
    assert response.status_code == 200, path
    return str(response.content.decode())


# --- The block


def test_the_block_is_a_required_chooser_of_the_snippet() -> None:
    chooser = ContentSnippetBlock().child_blocks["snippet"]
    assert isinstance(chooser, SnippetChooserBlock)
    assert chooser.target_model is ContentSnippet
    assert chooser.required


@pytest.mark.parametrize("model", [HomePage, StandardPage, BlogPostPage])
def test_the_block_is_in_every_page_body(model: type) -> None:
    body = model._meta.get_field("body")
    assert isinstance(body.stream_block.child_blocks["content_snippet"], ContentSnippetBlock)


def test_the_helper_that_builds_a_body_offers_the_block() -> None:
    assert "content_snippet" in body_field().stream_block.child_blocks


# --- Rendering in both themes


@pytest.mark.parametrize("theme", ["base", "ride"])
def test_a_live_snippet_renders_its_text_in_both_themes(theme: str) -> None:
    snippet = ContentSnippetFactory.create(body="<p>Free shipping over <b>50</b> euros.</p>")
    html = render(theme, snippet)
    assert "block-content-snippet" in html
    assert "Free shipping over <b>50</b> euros." in html
    for forbidden in ("<script", "style=", " onclick"):
        assert forbidden not in html


@pytest.mark.parametrize("theme", ["base", "ride"])
def test_an_unpublished_snippet_renders_nothing(theme: str) -> None:
    snippet = ContentSnippetFactory.create(body="<p>Hidden.</p>", live=False)
    assert render(theme, snippet) == ""


def test_a_snippet_that_was_unpublished_later_renders_nothing() -> None:
    snippet = ContentSnippetFactory.create(body="<p>Gone soon.</p>")
    assert "Gone soon." in render("base", snippet)
    snippet.unpublish()
    assert render("base", snippet) == ""


def test_the_text_goes_through_the_sanitiser() -> None:
    snippet = ContentSnippetFactory.create(
        body='<p>Fine</p><script>alert(1)</script><p onclick="x()">Text</p>'
    )
    snippet.refresh_from_db()
    html = render("base", snippet)
    assert "<script" not in html
    assert "onclick" not in html
    assert "Fine" in html


# --- Locale


def test_a_page_shows_the_snippets_own_translation() -> None:
    other = other_language()
    snippet = ContentSnippetFactory.create(body="<p>Original text.</p>")
    translated(snippet, other, "<p>Testo tradotto.</p>", live=True)
    assert "Testo tradotto." in render("base", snippet, other)
    assert "Original text." not in render("base", snippet, other)
    assert "Original text." in render("base", snippet)
    assert "Testo tradotto." not in render("base", snippet)


def test_a_translation_chosen_on_the_page_still_resolves_by_the_page_language() -> None:
    other = other_language()
    snippet = ContentSnippetFactory.create(body="<p>Original text.</p>")
    copy = translated(snippet, other, "<p>Testo tradotto.</p>", live=True)
    # the editor of a translated page may have picked the translation, or the source
    assert "Original text." in render("base", copy)
    assert "Testo tradotto." in render("base", copy, other)


def test_without_a_live_translation_the_page_falls_back_to_the_primary_language() -> None:
    other = other_language()
    snippet = ContentSnippetFactory.create(body="<p>Original text.</p>")
    assert "Original text." in render("base", snippet, other)
    translated(snippet, other, "<p>Bozza.</p>", live=False)
    html = render("base", snippet, other)
    assert "Original text." in html
    assert "Bozza." not in html


def test_a_snippet_never_shows_a_third_language() -> None:
    other = other_language()
    snippet = ContentSnippetFactory.create(body="<p>Original text.</p>", live=False)
    translated(snippet, other, "<p>Solo italiano.</p>", live=True)
    assert render("base", snippet) == ""  # the primary copy is not live: nothing, not the other
    assert "Solo italiano." in render("base", snippet, other)


# --- Editing reaches every page, after approval


def test_publishing_an_edit_changes_every_page_that_places_the_snippet() -> None:
    snippet = ContentSnippetFactory.create(body="<p>Old terms.</p>")
    first, second = page_with(snippet, title="One"), page_with(snippet, title="Two")
    assert "Old terms." in body_of(first.url)
    assert "Old terms." in body_of(second.url)
    publish_edit(snippet, "<p>New terms.</p>")
    for page in (first, second):
        html = body_of(page.url)
        assert "New terms." in html
        assert "Old terms." not in html


def test_a_draft_edit_does_not_reach_the_pages() -> None:
    snippet = ContentSnippetFactory.create(body="<p>Old terms.</p>")
    page = page_with(snippet)
    snippet.body = "<p>Draft terms.</p>"
    snippet.save_revision()
    html = body_of(page.url)
    assert "Old terms." in html
    assert "Draft terms." not in html


def test_unpublishing_the_snippet_removes_it_from_the_pages() -> None:
    snippet = ContentSnippetFactory.create(body="<p>Old terms.</p>")
    page = page_with(snippet)
    assert "Old terms." in body_of(page.url)
    snippet.unpublish()
    assert "Old terms." not in body_of(page.url)


def test_the_snippet_goes_through_a_moderation_workflow() -> None:
    snippet = ContentSnippetFactory.create()
    workflow = snippet.get_workflow()
    assert workflow is not None
    assert workflow.name == "Content snippet review"
    assert snippet.has_workflow
    assert workflow.workflow_tasks.count() == 1
    assert ContentSnippet.get_workflow is not None


def test_a_submission_leaves_the_live_text_alone_until_it_is_approved() -> None:
    from django.contrib.auth import get_user_model

    snippet = ContentSnippetFactory.create(body="<p>Old terms.</p>")
    page = page_with(snippet)
    user = get_user_model().objects.create_user("editor", "e@example.com", None)
    snippet.body = "<p>Proposed terms.</p>"
    revision = snippet.save_revision(user=user)
    state = snippet.get_workflow().start(snippet, user)
    assert state.status == WorkflowState.STATUS_IN_PROGRESS
    assert "Old terms." in body_of(page.url)
    assert revision.pk
    assert "Proposed terms." not in body_of(page.url)


# --- Where it is used


def test_the_editor_sees_the_pages_that_use_the_snippet() -> None:
    snippet = ContentSnippetFactory.create()
    page = page_with(snippet, title="Shipping rules")
    references = ReferenceIndex.get_references_to(snippet)
    assert references.exists()
    used_by = {ref.object_id for ref in references.all()}
    assert str(page.pk) in used_by


def test_the_usage_screen_lists_the_page(admin_client: Client) -> None:
    snippet = ContentSnippetFactory.create()
    page_with(snippet, title="Shipping rules")
    url = reverse("wagtailsnippets_cms_contentsnippet:usage", args=[snippet.pk])
    response = admin_client.get(url)
    assert response.status_code == 200
    assert "Shipping rules" in response.content.decode()


# --- No recursion


def test_a_snippet_holds_rich_text_only_so_it_cannot_contain_a_snippet() -> None:
    from wagtail.fields import RichTextField, StreamField

    fields = {f.name: f for f in ContentSnippet._meta.get_fields()}
    assert isinstance(fields["body"], RichTextField)
    assert not [f for f in fields.values() if isinstance(f, StreamField)]
    assert not [f for f in fields.values() if getattr(f, "related_model", None) is ContentSnippet]


def test_the_block_renders_a_snippet_text_as_text_even_if_it_names_the_block() -> None:
    snippet = ContentSnippetFactory.create(body='<p>{"content_snippet": 1}</p>')
    html = render("base", snippet)
    assert html.count("block-content-snippet") == 1


# --- Query bound


def test_a_page_costs_one_lookup_per_distinct_snippet() -> None:
    one = ContentSnippetFactory.create(body="<p>One.</p>")
    two = ContentSnippetFactory.create(body="<p>Two.</p>")
    block = ContentSnippetBlock()
    values = [block.to_python({"snippet": s.pk}) for s in (one, two, one, one, two, one)]
    page = _page_in(primary_language())
    with CaptureQueriesContext(connection) as queries:
        out = [str(block.render(v, context={"page": page})) for v in values]
    assert all("block-content-snippet" in html for html in out)
    assert len(queries) <= 2, [q["sql"] for q in queries]


# --- Caches


def test_a_publish_unpublish_and_delete_start_a_new_cache_generation() -> None:
    snippet = ContentSnippetFactory.create()
    before = snippet_cache.generation()
    publish_edit(snippet, "<p>Changed.</p>")
    after_publish = snippet_cache.generation()
    assert after_publish > before
    snippet.unpublish()
    after_unpublish = snippet_cache.generation()
    assert after_unpublish > after_publish
    snippet.delete()
    assert snippet_cache.generation() > after_unpublish


def test_a_draft_save_keeps_the_cache_generation() -> None:
    snippet = ContentSnippetFactory.create()
    before = snippet_cache.generation()
    snippet.body = "<p>Draft.</p>"
    snippet.save_revision()
    assert snippet_cache.generation() == before


def test_the_cache_key_of_a_fragment_follows_the_generation() -> None:
    cache.clear()
    key = snippet_cache.fragment_key(7, "it")
    snippet = ContentSnippetFactory.create()
    publish_edit(snippet, "<p>Changed.</p>")
    assert snippet_cache.fragment_key(7, "it") != key
    assert re.fullmatch(r"cms:snippet:\d+:7:it", snippet_cache.fragment_key(7, "it"))
