"""SEC.5 ReDoS: the embed and attribute scans of the vendor rich-text checks run in linear time."""

import time

import pytest

from apps.vendors.engine import (
    has_page_link,
    has_unsafe_link,
    image_embed_formats,
    media_references,
)

BUDGET = 2.0  # seconds; the quadratic versions need minutes on these inputs

HTML = (
    '<p>x <embed embedtype="image" id="12" format="left" alt=\'a b\'/> '
    "<a linktype='document' id=7>doc</a> <A HREF=\"https://ok.example/\">l</A>"
    '<a linktype="page" id="3">p</a> <a href=javascript:x>bad</a> <abbr>no</abbr></p>'
)


def test_media_references_keep_their_output() -> None:
    assert media_references(HTML) == (["12"], ["7"])


def test_image_embed_formats_keep_their_output() -> None:
    assert image_embed_formats(HTML) == ["left"]
    assert image_embed_formats("<embed embedtype='image'>") == [""]


def test_link_checks_keep_their_output() -> None:
    assert has_page_link(HTML)
    assert has_unsafe_link(HTML)
    assert not has_page_link('<a href="https://ok.example/">l</a>')
    assert not has_unsafe_link('<a href="https://ok.example/">l</a><embed embedtype="image">')


def test_empty_and_odd_values_are_read_as_before() -> None:
    assert media_references('<embed embedtype="image" id="">') == ([""], [])
    assert media_references("<embed -id=1 embedtype=image id=4>") == (["4"], [])
    assert media_references("<embed embedtype=image id=1 id=2>") == (["2"], [])


HOSTILE = [
    "<a " * 40_000,
    "<embed " * 20_000,
    "<a " + "a" * 100_000,
    "<a " + "a=" * 50_000,
    "<a " + 'a="' * 30_000,
    "<a " + "-" * 100_000 + "!>",
    "<a " + "a " * 50_000 + ">",
    "<" * 100_000,
]


@pytest.mark.parametrize("html", HOSTILE)
def test_rich_text_scans_are_linear_on_hostile_html(html: str) -> None:
    started = time.perf_counter()
    media_references(html)
    has_page_link(html)
    has_unsafe_link(html)
    image_embed_formats(html)
    assert time.perf_counter() - started < BUDGET
