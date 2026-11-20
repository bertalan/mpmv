"""SEC.5 ReDoS: the tag test of `paragraph_html` runs in linear time on hostile text."""

import time

import pytest

from apps.events.engine import _strip_tags, paragraph_html

BUDGET = 2.0  # seconds; the quadratic version needs minutes on these inputs


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("<p>Hello</p>", "<p>Hello</p>"),
        ("  <b>bold</b> text ", "<b>bold</b> text"),
        ("< /p>", "< /p>"),
        ("<  a href='x'>link", "<  a href='x'>link"),
        ("a < b and c > d", "a < b and c > d"),
        ("a < 1 and 2 > 0", "<p>a &lt; 1 and 2 &gt; 0</p>"),
        ("1 <2> 3", "<p>1 &lt;2&gt; 3</p>"),
        ("one\n\ntwo\nthree", "<p>one</p><p>two<br>three</p>"),
        ("<br", "<p>&lt;br</p>"),
    ],
)
def test_paragraph_html_keeps_its_output(text: str, expected: str) -> None:
    assert paragraph_html(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "<a " * 40_000,
        "<" + " " * 100_000 + "!",
        "<" * 100_000,
        "< /" * 40_000 + "!",
        "<x" * 50_000,
    ],
)
def test_paragraph_html_is_linear_on_hostile_text(text: str) -> None:
    started = time.perf_counter()
    paragraph_html(text)
    assert time.perf_counter() - started < BUDGET


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("<p>Hi <b>there</b></p>", " Hi  there  "),
        ("a < b > c", "a   c"),
        ("1 < 2", "1 < 2"),
        ("x <a href='1'>y</a> <br", "x  y  <br"),
        ("", ""),
    ],
)
def test_strip_tags_keeps_its_output(text: str, expected: str) -> None:
    assert _strip_tags(text) == expected


@pytest.mark.parametrize("text", ["<a " * 40_000, "<" + " " * 100_000 + "!", "<x" * 50_000])
def test_strip_tags_is_linear_on_hostile_text(text: str) -> None:
    started = time.perf_counter()
    _strip_tags(text)
    assert time.perf_counter() - started < BUDGET
