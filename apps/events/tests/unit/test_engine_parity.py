# ruff: noqa: E501
"""SEC.5 ReDoS rewrites: `paragraph_html`, `paragraph_text` and `_words` give the same output as before on
real event-body text."""

import pytest

from apps.events.engine import _words, paragraph_html, paragraph_text

# Expected values were taken from the implementation before the SEC.5 ReDoS rewrite; the inputs are valid, real-looking text.

HTML_CASES = [
    (
        "Join us on Saturday.\n\nBring a jacket.",
        "<p>Join us on Saturday.</p><p>Bring a jacket.</p>",
    ),
    (
        "<p>Doors open at <strong>18:00</strong>.</p><p>Free entry.</p>",
        "<p>Doors open at <strong>18:00</strong>.</p><p>Free entry.</p>",
    ),
    (
        "<h2>Programme</h2><ul><li>Ride out</li><li>Lunch &amp; music</li></ul>",
        "<h2>Programme</h2><ul><li>Ride out</li><li>Lunch &amp; music</li></ul>",
    ),
    ("Entry < 5 EUR and > 3 EUR for kids", "<p>Entry &lt; 5 EUR and &gt; 3 EUR for kids</p>"),
    ("Ticket price <10 euro, bring cash", "<p>Ticket price &lt;10 euro, bring cash</p>"),
    ("Line one<br/>line two", "Line one<br/>line two"),
    (
        '<a href="https://example.org/map?x=1&y=2" target="_blank">Map</a> to the start',
        '<a href="https://example.org/map?x=1&y=2" target="_blank">Map</a> to the start',
    ),
    ("5 < 6 and 7 > 2", "<p>5 &lt; 6 and 7 &gt; 2</p>"),
    ("< p>spaced</ p>", "< p>spaced</ p>"),
    ("a <3 b", "<p>a &lt;3 b</p>"),
    ("<p>Ride with us</p>\r\n<p>Second</p>", "<p>Ride with us</p>\r\n<p>Second</p>"),
    (
        '<img src="/m/1.jpg" alt="Start"><p>Caption &amp; credits</p>',
        '<img src="/m/1.jpg" alt="Start"><p>Caption &amp; credits</p>',
    ),
    (
        "Plain text with an accent: caffè e cornetti",
        "<p>Plain text with an accent: caffè e cornetti</p>",
    ),
    (
        "<div class=\"intro\" data-x='1'>Hello</div> tail",
        "<div class=\"intro\" data-x='1'>Hello</div> tail",
    ),
    ("x </ b> y", "x </ b> y"),
]
TEXT_CASES = [
    ("Join us on Saturday.\n\nBring a jacket.", "Join us on Saturday.\n\nBring a jacket."),
    (
        "<p>Doors open at <strong>18:00</strong>.</p><p>Free entry.</p>",
        "<p>Doors open at <strong>18:00</strong>.</p><p>Free entry.</p>",
    ),
    (
        "<h2>Programme</h2><ul><li>Ride out</li><li>Lunch &amp; music</li></ul>",
        "<h2>Programme</h2><ul><li>Ride out</li><li>Lunch &amp; music</li></ul>",
    ),
    ("Entry < 5 EUR and > 3 EUR for kids", "Entry < 5 EUR and > 3 EUR for kids"),
    ("Ticket price <10 euro, bring cash", "Ticket price <10 euro, bring cash"),
    ("Line one<br/>line two", "Line one<br/>line two"),
    (
        '<a href="https://example.org/map?x=1&y=2" target="_blank">Map</a> to the start',
        '<a href="https://example.org/map?x=1&y=2" target="_blank">Map</a> to the start',
    ),
    ("5 < 6 and 7 > 2", "5 < 6 and 7 > 2"),
    ("< p>spaced</ p>", "< p>spaced</ p>"),
    ("a <3 b", "a <3 b"),
    ("<p>Ride with us</p>\r\n<p>Second</p>", "Ride with us\n\nSecond"),
    (
        '<img src="/m/1.jpg" alt="Start"><p>Caption &amp; credits</p>',
        '<img src="/m/1.jpg" alt="Start"><p>Caption &amp; credits</p>',
    ),
    ("Plain text with an accent: caffè e cornetti", "Plain text with an accent: caffè e cornetti"),
    (
        "<div class=\"intro\" data-x='1'>Hello</div> tail",
        "<div class=\"intro\" data-x='1'>Hello</div> tail",
    ),
    ("x </ b> y", "x </ b> y"),
]
WORDS_CASES = [
    ("Join us on Saturday.\n\nBring a jacket.", "Join us on Saturday. Bring a jacket."),
    (
        "<p>Doors open at <strong>18:00</strong>.</p><p>Free entry.</p>",
        "Doors open at 18:00 . Free entry.",
    ),
    (
        "<h2>Programme</h2><ul><li>Ride out</li><li>Lunch &amp; music</li></ul>",
        "Programme Ride out Lunch & music",
    ),
    ("Entry < 5 EUR and > 3 EUR for kids", "Entry < 5 EUR and > 3 EUR for kids"),
    ("Ticket price <10 euro, bring cash", "Ticket price <10 euro, bring cash"),
    ("Line one<br/>line two", "Line one line two"),
    (
        '<a href="https://example.org/map?x=1&y=2" target="_blank">Map</a> to the start',
        "Map to the start",
    ),
    ("5 < 6 and 7 > 2", "5 < 6 and 7 > 2"),
    ("< p>spaced</ p>", "spaced"),
    ("a <3 b", "a <3 b"),
    ("<p>Ride with us</p>\r\n<p>Second</p>", "Ride with us Second"),
    ('<img src="/m/1.jpg" alt="Start"><p>Caption &amp; credits</p>', "Caption & credits"),
    ("Plain text with an accent: caffè e cornetti", "Plain text with an accent: caffè e cornetti"),
    ("<div class=\"intro\" data-x='1'>Hello</div> tail", "Hello tail"),
    ("x </ b> y", "x y"),
]
SHORT_WORDS_CASES = [
    ("Join us on Saturday.\n\nBring a jacket.", "Join us on…"),
    ("<p>Doors open at <strong>18:00</strong>.</p><p>Free entry.</p>", "Doors open…"),
    ("<h2>Programme</h2><ul><li>Ride out</li><li>Lunch &amp; music</li></ul>", "Programme R…"),
    ("Entry < 5 EUR and > 3 EUR for kids", "Entry < 5 E…"),
    ("Ticket price <10 euro, bring cash", "Ticket pric…"),
    ("Line one<br/>line two", "Line one li…"),
    (
        '<a href="https://example.org/map?x=1&y=2" target="_blank">Map</a> to the start',
        "Map to the…",
    ),
    ("5 < 6 and 7 > 2", "5 < 6 and 7…"),
    ("< p>spaced</ p>", "spaced"),
    ("a <3 b", "a <3 b"),
    ("<p>Ride with us</p>\r\n<p>Second</p>", "Ride with u…"),
    ('<img src="/m/1.jpg" alt="Start"><p>Caption &amp; credits</p>', "Caption & c…"),
    ("Plain text with an accent: caffè e cornetti", "Plain text…"),
    ("<div class=\"intro\" data-x='1'>Hello</div> tail", "Hello tail"),
    ("x </ b> y", "x y"),
]


@pytest.mark.parametrize(("text", "expected"), HTML_CASES)
def test_paragraph_html_parity(text: str, expected: str) -> None:
    assert paragraph_html(text) == expected


@pytest.mark.parametrize(("text", "expected"), TEXT_CASES)
def test_paragraph_text_parity(text: str, expected: str) -> None:
    assert paragraph_text(paragraph_html(text)) == expected


@pytest.mark.parametrize(("text", "expected"), WORDS_CASES)
def test_words_parity(text: str, expected: str) -> None:
    assert _words(text) == expected


@pytest.mark.parametrize(("text", "expected"), SHORT_WORDS_CASES)
def test_words_parity_with_a_short_limit(text: str, expected: str) -> None:
    assert _words(text, 12) == expected
