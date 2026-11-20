# ruff: noqa: E501
"""SEC.5 ReDoS rewrites: the attribute parser behind `media_references`, `has_page_link`, `has_unsafe_link`
and `image_embed_formats` gives the same output as before on real embed codes and links."""

import pytest

from apps.vendors.engine import (
    has_page_link,
    has_unsafe_link,
    image_embed_formats,
    media_references,
)

# Expected values were taken from the implementation before the SEC.5 ReDoS rewrite; the inputs are valid, real-looking text.

MEDIA = [
    (
        '<p>Watch: <iframe width="560" height="315" src="https://www.youtube.com/embed/dQw4w9WgXcQ" frameborder="0" allowfullscreen></iframe></p>',
        [[], []],
    ),
    (
        '<iframe src="https://player.vimeo.com/video/76979871?h=8272103f6e" width="640" height="360"></iframe>',
        [[], []],
    ),
    (
        '<p><embed alt="Front" embedtype="image" format="left" id="12"/></p><p>text <embed alt="Rear" embedtype="image" format="fullwidth" id="13"/></p>',
        [["12", "13"], []],
    ),
    ('<embed embedtype="image" id="7" alt="No format">', [["7"], []]),
    ("<embed embedtype='image' id='8' format='right' alt='single quotes'/>", [["8"], []]),
    ("<embed embedtype=image id=9 format=left />", [["9"], []]),
    ('<EMBED EmbedType="IMAGE" ID="10" Format="left"/>', [["10"], []]),
    (
        '<img src="/m/a-400.jpg" srcset="/m/a-400.jpg 400w, /m/a-800.jpg 800w" sizes="(max-width: 600px) 400px, 800px" alt="Bike">',
        [[], []],
    ),
    ('<a href="https://example.org/page">Safe</a>', [[], []]),
    ('<a href="http://example.org/page">Plain http</a>', [[], []]),
    ("<a href='https://example.org/a?x=1&amp;y=2'>Single quotes</a>", [[], []]),
    ("<a href=https://example.org/unquoted>Unquoted</a>", [[], []]),
    ('<a href="https://a.example/1" href="http://b.example/2">Dup, last wins</a>', [[], []]),
    ('<a href="http://a.example/1" href="https://b.example/2">Dup, last wins safe</a>', [[], []]),
    ('<a linktype="page" id="5">Page</a>', [[], []]),
    ('<a id="5" linktype=page>Page unquoted</a>', [[], []]),
    ("<a linktype='page' id='5'>Page single</a>", [[], []]),
    (
        '<a linktype="document" id="3">Doc</a> and <a linktype="document" id=4>Doc 2</a>',
        [[], ["3", "4"]],
    ),
    ('<a linktype="document" id="3" linktype="page">Dup linktype</a>', [[], []]),
    ('<a LinkType="Page" id="2">Mixed case</a>', [[], []]),
    ('<a href="mailto:info@example.org">Mail</a>', [[], []]),
    ('<a href="/relative/path">Relative</a>', [[], []]),
    ('<a href="//cdn.example.org/x">Scheme relative</a>', [[], []]),
    ('<a name="top" data-x-y="1" href="https://example.org">Anchor</a>', [[], []]),
    ("<a href = \"https://example.org\" title = 't'>Spaces around equals</a>", [[], []]),
    ("<p>No tags here</p>", [[], []]),
    ('<a href="https://example.org">Open', [[], []]),
    ('<embed embedtype="image" id="1" embedtype="media" format="left">', [[], []]),
    ('<a data-linktype="page" id="1">prefixed name</a>', [[], []]),
    ('<a href="">empty</a>', [[], []]),
    ("<a href>boolean</a>", [[], []]),
    ('<a :x="1" _y=2 href="https://e.org">odd names</a>', [[], []]),
]
PAGE_LINK = [
    (
        '<p>Watch: <iframe width="560" height="315" src="https://www.youtube.com/embed/dQw4w9WgXcQ" frameborder="0" allowfullscreen></iframe></p>',
        False,
    ),
    (
        '<iframe src="https://player.vimeo.com/video/76979871?h=8272103f6e" width="640" height="360"></iframe>',
        False,
    ),
    (
        '<p><embed alt="Front" embedtype="image" format="left" id="12"/></p><p>text <embed alt="Rear" embedtype="image" format="fullwidth" id="13"/></p>',
        False,
    ),
    ('<embed embedtype="image" id="7" alt="No format">', False),
    ("<embed embedtype='image' id='8' format='right' alt='single quotes'/>", False),
    ("<embed embedtype=image id=9 format=left />", False),
    ('<EMBED EmbedType="IMAGE" ID="10" Format="left"/>', False),
    (
        '<img src="/m/a-400.jpg" srcset="/m/a-400.jpg 400w, /m/a-800.jpg 800w" sizes="(max-width: 600px) 400px, 800px" alt="Bike">',
        False,
    ),
    ('<a href="https://example.org/page">Safe</a>', False),
    ('<a href="http://example.org/page">Plain http</a>', False),
    ("<a href='https://example.org/a?x=1&amp;y=2'>Single quotes</a>", False),
    ("<a href=https://example.org/unquoted>Unquoted</a>", False),
    ('<a href="https://a.example/1" href="http://b.example/2">Dup, last wins</a>', False),
    ('<a href="http://a.example/1" href="https://b.example/2">Dup, last wins safe</a>', False),
    ('<a linktype="page" id="5">Page</a>', True),
    ('<a id="5" linktype=page>Page unquoted</a>', True),
    ("<a linktype='page' id='5'>Page single</a>", True),
    ('<a linktype="document" id="3">Doc</a> and <a linktype="document" id=4>Doc 2</a>', False),
    ('<a linktype="document" id="3" linktype="page">Dup linktype</a>', True),
    ('<a LinkType="Page" id="2">Mixed case</a>', True),
    ('<a href="mailto:info@example.org">Mail</a>', False),
    ('<a href="/relative/path">Relative</a>', False),
    ('<a href="//cdn.example.org/x">Scheme relative</a>', False),
    ('<a name="top" data-x-y="1" href="https://example.org">Anchor</a>', False),
    ("<a href = \"https://example.org\" title = 't'>Spaces around equals</a>", False),
    ("<p>No tags here</p>", False),
    ('<a href="https://example.org">Open', False),
    ('<embed embedtype="image" id="1" embedtype="media" format="left">', False),
    ('<a data-linktype="page" id="1">prefixed name</a>', False),
    ('<a href="">empty</a>', False),
    ("<a href>boolean</a>", False),
    ('<a :x="1" _y=2 href="https://e.org">odd names</a>', False),
]
UNSAFE_LINK = [
    (
        '<p>Watch: <iframe width="560" height="315" src="https://www.youtube.com/embed/dQw4w9WgXcQ" frameborder="0" allowfullscreen></iframe></p>',
        False,
    ),
    (
        '<iframe src="https://player.vimeo.com/video/76979871?h=8272103f6e" width="640" height="360"></iframe>',
        False,
    ),
    (
        '<p><embed alt="Front" embedtype="image" format="left" id="12"/></p><p>text <embed alt="Rear" embedtype="image" format="fullwidth" id="13"/></p>',
        False,
    ),
    ('<embed embedtype="image" id="7" alt="No format">', False),
    ("<embed embedtype='image' id='8' format='right' alt='single quotes'/>", False),
    ("<embed embedtype=image id=9 format=left />", False),
    ('<EMBED EmbedType="IMAGE" ID="10" Format="left"/>', False),
    (
        '<img src="/m/a-400.jpg" srcset="/m/a-400.jpg 400w, /m/a-800.jpg 800w" sizes="(max-width: 600px) 400px, 800px" alt="Bike">',
        False,
    ),
    ('<a href="https://example.org/page">Safe</a>', False),
    ('<a href="http://example.org/page">Plain http</a>', True),
    ("<a href='https://example.org/a?x=1&amp;y=2'>Single quotes</a>", False),
    ("<a href=https://example.org/unquoted>Unquoted</a>", True),
    ('<a href="https://a.example/1" href="http://b.example/2">Dup, last wins</a>', True),
    ('<a href="http://a.example/1" href="https://b.example/2">Dup, last wins safe</a>', False),
    ('<a linktype="page" id="5">Page</a>', False),
    ('<a id="5" linktype=page>Page unquoted</a>', False),
    ("<a linktype='page' id='5'>Page single</a>", False),
    ('<a linktype="document" id="3">Doc</a> and <a linktype="document" id=4>Doc 2</a>', False),
    ('<a linktype="document" id="3" linktype="page">Dup linktype</a>', False),
    ('<a LinkType="Page" id="2">Mixed case</a>', False),
    ('<a href="mailto:info@example.org">Mail</a>', True),
    ('<a href="/relative/path">Relative</a>', True),
    ('<a href="//cdn.example.org/x">Scheme relative</a>', True),
    ('<a name="top" data-x-y="1" href="https://example.org">Anchor</a>', False),
    ("<a href = \"https://example.org\" title = 't'>Spaces around equals</a>", False),
    ("<p>No tags here</p>", False),
    ('<a href="https://example.org">Open', False),
    ('<embed embedtype="image" id="1" embedtype="media" format="left">', False),
    ('<a data-linktype="page" id="1">prefixed name</a>', False),
    ('<a href="">empty</a>', True),
    ("<a href>boolean</a>", False),
    ('<a :x="1" _y=2 href="https://e.org">odd names</a>', False),
]
EMBED_FORMATS = [
    (
        '<p>Watch: <iframe width="560" height="315" src="https://www.youtube.com/embed/dQw4w9WgXcQ" frameborder="0" allowfullscreen></iframe></p>',
        [],
    ),
    (
        '<iframe src="https://player.vimeo.com/video/76979871?h=8272103f6e" width="640" height="360"></iframe>',
        [],
    ),
    (
        '<p><embed alt="Front" embedtype="image" format="left" id="12"/></p><p>text <embed alt="Rear" embedtype="image" format="fullwidth" id="13"/></p>',
        ["left", "fullwidth"],
    ),
    ('<embed embedtype="image" id="7" alt="No format">', [""]),
    ("<embed embedtype='image' id='8' format='right' alt='single quotes'/>", ["right"]),
    ("<embed embedtype=image id=9 format=left />", ["left"]),
    ('<EMBED EmbedType="IMAGE" ID="10" Format="left"/>', ["left"]),
    (
        '<img src="/m/a-400.jpg" srcset="/m/a-400.jpg 400w, /m/a-800.jpg 800w" sizes="(max-width: 600px) 400px, 800px" alt="Bike">',
        [],
    ),
    ('<a href="https://example.org/page">Safe</a>', []),
    ('<a href="http://example.org/page">Plain http</a>', []),
    ("<a href='https://example.org/a?x=1&amp;y=2'>Single quotes</a>", []),
    ("<a href=https://example.org/unquoted>Unquoted</a>", []),
    ('<a href="https://a.example/1" href="http://b.example/2">Dup, last wins</a>', []),
    ('<a href="http://a.example/1" href="https://b.example/2">Dup, last wins safe</a>', []),
    ('<a linktype="page" id="5">Page</a>', []),
    ('<a id="5" linktype=page>Page unquoted</a>', []),
    ("<a linktype='page' id='5'>Page single</a>", []),
    ('<a linktype="document" id="3">Doc</a> and <a linktype="document" id=4>Doc 2</a>', []),
    ('<a linktype="document" id="3" linktype="page">Dup linktype</a>', []),
    ('<a LinkType="Page" id="2">Mixed case</a>', []),
    ('<a href="mailto:info@example.org">Mail</a>', []),
    ('<a href="/relative/path">Relative</a>', []),
    ('<a href="//cdn.example.org/x">Scheme relative</a>', []),
    ('<a name="top" data-x-y="1" href="https://example.org">Anchor</a>', []),
    ("<a href = \"https://example.org\" title = 't'>Spaces around equals</a>", []),
    ("<p>No tags here</p>", []),
    ('<a href="https://example.org">Open', []),
    ('<embed embedtype="image" id="1" embedtype="media" format="left">', []),
    ('<a data-linktype="page" id="1">prefixed name</a>', []),
    ('<a href="">empty</a>', []),
    ("<a href>boolean</a>", []),
    ('<a :x="1" _y=2 href="https://e.org">odd names</a>', []),
]


@pytest.mark.parametrize(("html", "expected"), MEDIA)
def test_media_references_parity(html: str, expected: list[list[str]]) -> None:
    images, documents = media_references(html)
    assert [images, documents] == expected


@pytest.mark.parametrize(("html", "expected"), PAGE_LINK)
def test_has_page_link_parity(html: str, expected: bool) -> None:
    assert has_page_link(html) is expected


@pytest.mark.parametrize(("html", "expected"), UNSAFE_LINK)
def test_has_unsafe_link_parity(html: str, expected: bool) -> None:
    assert has_unsafe_link(html) is expected


@pytest.mark.parametrize(("html", "expected"), EMBED_FORMATS)
def test_image_embed_formats_parity(html: str, expected: list[str]) -> None:
    assert image_embed_formats(html) == expected
