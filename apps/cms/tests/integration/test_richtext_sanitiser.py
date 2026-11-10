"""CMS.12: rich text written outside the editor is sanitised at the model boundary.

Done when: a page saved with hostile rich text (import, seed, API, MIG.7: any `save()` that does
not come from the editor) is stored with only the editor's allowed elements and attributes, in a
`RichTextField` and in every `RichTextBlock` of a StreamField. A clean text is stored byte for
byte, and a second save changes nothing.
"""

import json

import pytest
from wagtail.rich_text import expand_db_html

from apps.cms.models import BlogIndexPage, StandardPage
from apps.cms.tests.factories import BlogIndexPageFactory, HomePageFactory, StandardPageFactory
from apps.events.models import EventIndexPage
from tests import hostile_input as hi

pytestmark = pytest.mark.django_db

HOSTILE = hi.HTML[1] + hi.HTML[5] + hi.HTML[7] + hi.HTML[10] + "<p>kept</p>"


def test_a_rich_text_field_is_stored_without_active_markup() -> None:
    page = BlogIndexPageFactory.create(intro=HOSTILE)

    stored = BlogIndexPage.objects.get(pk=page.pk).intro

    assert hi.active_markup(expand_db_html(stored)) == []
    assert "kept" in stored


def test_the_instance_saved_holds_the_clean_text_too() -> None:
    page = BlogIndexPageFactory.create(intro=HOSTILE)

    assert hi.active_markup(page.intro) == []


def test_a_stream_rich_text_block_is_stored_without_active_markup() -> None:
    page = StandardPageFactory.create(body=[("paragraph", HOSTILE)])

    stored = json.dumps(list(StandardPage.objects.get(pk=page.pk).body.raw_data))

    assert hi.active_markup(expand_db_html(stored)) == []
    assert "kept" in stored


def test_the_events_app_is_covered() -> None:
    home = HomePageFactory.create()
    index = home.add_child(instance=EventIndexPage(title="Events", slug="events", intro=HOSTILE))

    assert hi.active_markup(EventIndexPage.objects.get(pk=index.pk).intro) == []


def test_a_clean_text_is_stored_byte_for_byte() -> None:
    clean = '<h2>T</h2><p>a <b>b</b> <a href="https://example.org/">l</a> &amp; c</p>'
    page = BlogIndexPageFactory.create(intro=clean)

    assert BlogIndexPage.objects.get(pk=page.pk).intro == clean


def test_a_second_save_changes_nothing() -> None:
    page = BlogIndexPageFactory.create(intro=HOSTILE)
    first = BlogIndexPage.objects.get(pk=page.pk).intro

    page.save()

    assert BlogIndexPage.objects.get(pk=page.pk).intro == first
