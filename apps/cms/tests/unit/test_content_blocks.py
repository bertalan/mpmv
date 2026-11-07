"""CMS.2 (part 1/2): hero, call to action, FAQ and rich text blocks (spec 10 §10.2, spec 14).

Done when: "An editor builds the home page without code". These are the blocks of the page body
(`body_field`); block templates are minimal, semantic and CSP-safe (no inline script or style)
until the `base` theme (THM.3, THM.4) restyles them. The FAQ is an accessible disclosure
(`<details>`) with FAQPage structured data (spec 10: "FAQ (with FAQPage schema)").
"""

import json
import re

import pytest
from django.core.exceptions import ValidationError
from wagtail import blocks
from wagtail.images.blocks import ImageChooserBlock

from apps.cms.blocks import CallToActionBlock, FaqBlock, HeroBlock, LinkBlock
from apps.cms.engine import faq_page_jsonld, jsonld_text
from apps.cms.models import body_field

# --- The blocks of a page body


def test_the_page_body_offers_rich_text_images_hero_cta_and_faq() -> None:
    children = body_field().stream_block.child_blocks
    assert isinstance(children["paragraph"], blocks.RichTextBlock)
    assert isinstance(children["hero"], HeroBlock)
    assert isinstance(children["cta"], CallToActionBlock)
    assert isinstance(children["faq"], FaqBlock)
    assert {"figure", "gallery"} <= set(children)


def test_the_hero_has_a_heading_an_optional_image_with_alt_text_and_up_to_two_links() -> None:
    children = HeroBlock().child_blocks
    assert children["heading"].required
    assert not children["text"].required
    assert isinstance(children["image"], ImageChooserBlock)
    assert not children["image"].required
    assert isinstance(children["alt_text"], blocks.CharBlock)
    links = children["links"]
    assert isinstance(links, blocks.ListBlock)
    assert isinstance(links.child_block, LinkBlock)
    assert links.meta.max_num == 2


def test_a_faq_has_questions_with_rich_text_answers() -> None:
    items = FaqBlock().child_blocks["items"]
    assert isinstance(items, blocks.ListBlock)
    assert items.meta.min_num == 1
    item = items.child_block.child_blocks
    assert isinstance(item["question"], blocks.CharBlock)
    assert isinstance(item["answer"], blocks.RichTextBlock)


# --- Links: a page of the site or an external URL, never both


@pytest.mark.parametrize(
    "value",
    [
        {"page": None, "url": "", "label": "Shop now"},  # no target
        {"page": None, "url": "https://example.com/", "label": ""},  # no label
        {"page": None, "url": "javascript:alert(1)", "label": "Shop now"},
    ],
)
def test_a_link_needs_a_label_and_one_valid_target(value: dict[str, object]) -> None:
    block = LinkBlock()
    with pytest.raises(ValidationError):
        block.clean(block.to_python(value))


def test_a_link_to_an_external_url_is_valid() -> None:
    block = LinkBlock()
    value = block.clean(block.to_python({"url": "https://example.com/", "label": "Read more"}))
    assert value.href() == "https://example.com/"


def test_a_hero_without_an_image_needs_no_alt_text() -> None:
    block = HeroBlock()
    value = block.clean(block.to_python({"heading": "Welcome", "text": "", "links": []}))
    assert value["heading"] == "Welcome"


# --- Rendering: semantic, CSP-safe markup


def _render(block: blocks.Block, raw: dict[str, object]) -> str:
    html: str = block.render(block.to_python(raw))
    return html


def _assert_csp_safe(html: str) -> None:
    assert "style=" not in html
    assert not re.search(r"\son[a-z]+=", html)
    assert "javascript:" not in html
    for script in re.findall(r"<script[^>]*>", html):
        assert 'type="application/ld+json"' in script, script


def test_a_hero_renders_its_heading_text_and_links() -> None:
    html = _render(
        HeroBlock(),
        {
            "heading": "Handmade goods",
            "text": "From small workshops.",
            "links": [{"page": None, "url": "https://example.com/shop/", "label": "Shop now"}],
        },
    )
    assert re.search(r"<section[^>]*class=\"[^\"]*hero", html)
    assert "<h2>Handmade goods</h2>" in html
    assert "From small workshops." in html
    assert '<a href="https://example.com/shop/"' in html
    assert ">Shop now</a>" in html
    assert "<img" not in html
    _assert_csp_safe(html)


def test_a_call_to_action_renders_its_heading_and_link() -> None:
    html = _render(
        CallToActionBlock(),
        {
            "heading": "Sell with us",
            "text": "Open your shop in a day.",
            "link": {"page": None, "url": "https://example.com/apply/", "label": "Apply"},
        },
    )
    assert "<h2>Sell with us</h2>" in html
    assert "Open your shop in a day." in html
    assert '<a href="https://example.com/apply/"' in html
    _assert_csp_safe(html)


FAQ = {
    "heading": "Questions",
    "items": [
        {"question": "Do you ship abroad?", "answer": "<p>Yes, to <b>most</b> countries.</p>"},
        {"question": "Can I return </script> items?", "answer": "<p>Within 14 days.</p>"},
    ],
}


def test_a_faq_renders_each_question_as_a_disclosure() -> None:
    html = _render(FaqBlock(), FAQ)
    assert "<h2>Questions</h2>" in html
    summaries = re.findall(r"<details[^>]*>\s*<summary>(.*?)</summary>", html, re.S)
    assert summaries == ["Do you ship abroad?", "Can I return &lt;/script&gt; items?"]
    assert "<b>most</b>" in html
    _assert_csp_safe(html)


def test_a_faq_carries_faqpage_structured_data() -> None:
    html = _render(FaqBlock(), FAQ)
    scripts = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    assert len(scripts) == 1
    assert "</script" not in scripts[0]  # escaped, the data cannot close its script element
    data = json.loads(scripts[0])
    assert data["@type"] == "FAQPage"
    assert [q["name"] for q in data["mainEntity"]] == [
        "Do you ship abroad?",
        "Can I return </script> items?",
    ]


# --- Pure functions


def test_faq_page_jsonld_lists_each_question_with_its_accepted_answer() -> None:
    data = faq_page_jsonld([("Do you ship?", "<p>Yes.</p>")])
    assert data == {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": "Do you ship?",
                "acceptedAnswer": {"@type": "Answer", "text": "<p>Yes.</p>"},
            }
        ],
    }


def test_jsonld_text_escapes_markup_characters() -> None:
    text = jsonld_text({"name": "</script><b>&"})
    for char in "<>&":
        assert char not in text
    assert json.loads(text) == {"name": "</script><b>&"}
