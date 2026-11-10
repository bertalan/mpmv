"""CMS.12: the server-side rich-text sanitiser (pure rule, spec 10 §10.2).

Done when: rich text written outside the editor is stored with only the allowed elements and
attributes, and renders no active markup. The allow-list is the editor's: the features of
`RICH_TEXT_FEATURES`.
"""

import re

import pytest

from apps.cms.blocks import RICH_TEXT_FEATURES
from apps.cms.engine import sanitise_rich_text
from tests import hostile_input as hi

pytestmark = pytest.mark.unit

ALLOWED_TAGS = {"p", "br", "h2", "h3", "b", "i", "ol", "ul", "li", "a"}


@pytest.mark.parametrize("value", hi.values("html", "word_html", "template", "bidi"))
def test_the_corpus_leaves_no_active_markup_and_only_allowed_elements(value: str) -> None:
    clean = sanitise_rich_text(value, RICH_TEXT_FEATURES)

    assert hi.active_markup(clean) == []
    assert set(re.findall(r"<\s*([a-z0-9]+)", clean)) <= ALLOWED_TAGS
    assert not re.search(r"<[^>]*javascript:", clean, re.IGNORECASE)
    assert not re.search(r"<[^>]*\son[a-z]+\s*=", clean, re.IGNORECASE)


@pytest.mark.parametrize("value", hi.values("html", "word_html", "template", "bidi"))
def test_sanitising_twice_changes_nothing(value: str) -> None:
    once = sanitise_rich_text(value, RICH_TEXT_FEATURES)

    assert sanitise_rich_text(once, RICH_TEXT_FEATURES) == once


def test_the_editors_own_formatting_survives() -> None:
    html = (
        "<h2>Title</h2><h3>Sub</h3><p>Some <b>bold</b> and <i>italic</i> text "
        '<a href="https://example.org/x?a=1&amp;b=2">a link</a></p>'
        "<ul><li>one</li><li>two</li></ul><ol><li>first</li></ol>"
    )

    assert sanitise_rich_text(html, RICH_TEXT_FEATURES) == html


def test_internal_links_survive() -> None:
    html = '<p><a id="3" linktype="page">page</a></p>'

    assert sanitise_rich_text(html, RICH_TEXT_FEATURES) == html


def test_existing_entities_are_not_escaped_twice() -> None:
    html = "<p>Fish &amp; chips &lt;3 &quot;quoted&quot;</p>"

    assert sanitise_rich_text(html, RICH_TEXT_FEATURES) == html


def test_an_element_outside_the_features_is_dropped_but_its_text_is_kept() -> None:
    clean = sanitise_rich_text("<p>before</p><p><img src=x></p><p>after</p>", RICH_TEXT_FEATURES)

    assert "img" not in clean
    assert "before" in clean
    assert "after" in clean


def test_a_feature_not_granted_is_not_kept() -> None:
    assert "<b>" not in sanitise_rich_text("<p><b>x</b></p>", ["italic"])
    assert "<h2>" not in sanitise_rich_text("<h2>x</h2>", ["h3"])


def test_a_data_or_script_url_is_not_kept_in_a_link() -> None:
    for url in ("javascript:alert(1)", "data:text/html,<b>x</b>", "vbscript:x"):
        clean = sanitise_rich_text(f'<p><a href="{url}">x</a></p>', RICH_TEXT_FEATURES)

        assert hi.active_markup(clean) == []
        assert "javascript:" not in clean
        assert "data:" not in clean


def test_an_empty_value_stays_empty() -> None:
    assert sanitise_rich_text("", RICH_TEXT_FEATURES) == ""
