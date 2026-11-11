"""EVT.3 security fix: the pure rules for vendor-authored event content (spec 15 "Vendor-safe
block subset"): image references, CTA links, embeds and size limits."""

import pytest

from apps.events import engine


def test_image_ids_come_from_figure_and_gallery_blocks_only() -> None:
    raw = [
        {"type": "figure", "value": {"image": 4, "alt_text": "a"}},
        {
            "type": "gallery",
            "value": {
                "images": [
                    {"type": "item", "value": {"image": 5, "alt_text": "b"}},
                    {"image": 6, "alt_text": "c"},
                ]
            },
        },
        {"type": "paragraph", "value": "<p>image 9</p>"},
    ]
    assert sorted(engine.image_ids(raw)) == [4, 5, 6]


def test_cta_links_are_listed() -> None:
    raw = [{"type": "cta", "value": {"heading": "h", "link": {"url": "https://x.example"}}}]
    assert engine.cta_links(raw) == [{"url": "https://x.example"}]
    assert engine.cta_links([{"type": "paragraph", "value": "x"}]) == []


def test_a_vendor_cta_url_is_https_only() -> None:
    assert engine.cta_url_problem("https://shop.example/a") is None
    assert engine.cta_url_problem("HTTPS://shop.example/a") is None
    for bad in ("http://shop.example", "javascript:alert(1)", "data:text/html,x", "//evil.example"):
        assert engine.cta_url_problem(bad) is not None
    assert engine.cta_url_problem("https://") is not None


def test_vendor_rich_text_has_no_embeds_or_document_links() -> None:
    assert engine.has_foreign_reference('<p><embed embedtype="image" id="3"/></p>')
    assert engine.has_foreign_reference('<a linktype="document" id="3">x</a>')
    assert not engine.has_foreign_reference('<p><a href="https://x.example">x</a></p>')


def test_vendor_collection_name_is_derived_from_the_public_id() -> None:
    assert engine.vendor_collection_name("abc") == "vendor-abc"


def test_body_limits() -> None:
    ok = [{"type": "paragraph", "value": "<p>x</p>"}] * engine.MAX_BODY_BLOCKS
    assert engine.body_limit_problem(ok) is None
    assert engine.body_limit_problem([*ok, ok[0]]) is not None
    long_text = [{"type": "paragraph", "value": "x" * (engine.MAX_TEXT_LENGTH + 1)}]
    assert engine.body_limit_problem(long_text) is not None
    many = [{"type": "gallery", "value": {"images": [{"image": 1}] * (engine.MAX_LIST_ITEMS + 1)}}]
    assert engine.body_limit_problem(many) is not None


def test_a_cta_url_refuses_userinfo() -> None:
    assert engine.cta_url_problem("https://shop.example@evil.example/") is not None
    assert engine.cta_url_problem("https://user:pw@evil.example/") is not None


def test_body_depth_is_capped() -> None:
    value: object = "x"
    for _ in range(engine.MAX_DEPTH + 2):
        value = {"k": value}
    assert engine.body_limit_problem([{"type": "paragraph", "value": value}]) is not None


@pytest.mark.parametrize(
    "char",
    [
        "\x7f",
        "\x85",
        "\x9f",
        "\xa0",
        "\u2028",
        "\u2029",
        "\u200b",
        "\u202a",
        "\u202e",
        "\u2066",
        "\u2069",
        "\x00",
        "\t",
        "\n",
    ],
)
def test_a_vendor_url_refuses_control_space_and_bidi_characters(char: str) -> None:
    for url in (
        f"https://a.example/x{char}y",
        f"https://a.example{char}",
        f"https://a{char}.example",
    ):
        assert engine.cta_url_problem(url) is not None, repr(url)
        assert engine.https_url_problem(url) is not None, repr(url)


def test_a_vendor_url_allows_a_plain_https_address() -> None:
    assert engine.https_url_problem("https://shop.example/a?b=1#c") is None
    assert engine.https_url_problem("https://shop.example/caf\u00e9") is None
    assert engine.https_url_problem("https://user@shop.example/") is not None
