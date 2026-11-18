"""CMS.16 (D-36): the `accordion` block, titled sections on native `<details>` and `<summary>`.
Closed by default, no script, rich text through the CMS.12 sanitiser, no FAQPage data."""

import re
from typing import Any

import pytest
from django.test.utils import override_settings
from django.utils import translation
from wagtail import blocks

from apps.cms.blocks import AccordionBlock
from apps.cms.engine import faq_questions
from apps.cms.models import BlogPostPage, HomePage, StandardPage, body_field
from apps.cms.tests.factories import StandardPageFactory
from tests import hostile_input as hi

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("english_interface")]


def value(open_first: bool = False, **extra: Any) -> Any:
    sections = [
        {"title": "Shipping", "body": "<p>Two days.</p>"},
        {"title": "Returns", "body": "<p>Thirty days.</p>"},
    ]
    return AccordionBlock().to_python({"sections": sections, "open_first": open_first, **extra})


def render(theme: str, val: Any) -> str:
    with override_settings(MPMV_THEME_DEFAULT=theme), translation.override("en"):
        return str(AccordionBlock().render(val, context={})).strip()


def test_the_block_has_sections_with_a_title_and_rich_text_and_an_open_first_flag() -> None:
    children = AccordionBlock().child_blocks
    item = children["sections"].child_block
    assert isinstance(item.child_blocks["title"], blocks.CharBlock)
    assert item.child_blocks["title"].required
    assert isinstance(item.child_blocks["body"], blocks.RichTextBlock)
    assert isinstance(children["open_first"], blocks.BooleanBlock)
    assert not children["open_first"].required
    assert children["sections"].meta.min_num == 1


@pytest.mark.parametrize("theme", ["base", "ride"])
def test_each_section_is_a_details_with_a_summary_and_no_script(theme: str) -> None:
    html = render(theme, value())
    assert html.count("<details") == 2
    assert len(re.findall(r"<summary\b[^>]*>\s*Shipping\s*</summary>", html)) == 1
    assert "Two days." in html
    for forbidden in ("<script", "style=", " onclick", "<button"):
        assert forbidden not in html


@pytest.mark.parametrize("theme", ["base", "ride"])
def test_sections_are_closed_by_default(theme: str) -> None:
    assert " open" not in render(theme, value())


@pytest.mark.parametrize("theme", ["base", "ride"])
def test_open_first_opens_only_the_first_section(theme: str) -> None:
    html = render(theme, value(open_first=True))
    details = re.findall(r"<details\b[^>]*>", html)
    assert [bool(re.search(r"\bopen\b", tag)) for tag in details] == [True, False]


@pytest.mark.parametrize("theme", ["base", "ride"])
def test_a_hostile_title_and_body_are_escaped_or_sanitised(theme: str) -> None:
    page = StandardPageFactory.create(
        body=[
            (
                "accordion",
                {
                    "sections": [
                        {
                            "title": "<script>alert(1)</script><b>t</b>",
                            "body": hi.HTML[1] + hi.HTML[5],
                        }
                    ],
                    "open_first": False,
                },
            )
        ]
    )
    html = render(theme, page.body[0].value)
    assert hi.active_markup(html) == []
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_an_accordion_gives_no_faq_questions() -> None:
    page = StandardPageFactory.create(body=[("accordion", dict(value().items()))])
    assert faq_questions(page) == []


@pytest.mark.parametrize("model", [HomePage, StandardPage, BlogPostPage])
def test_the_page_bodies_offer_the_block(model: Any) -> None:
    assert "accordion" in model._meta.get_field("body").stream_block.child_blocks


def test_the_shared_body_field_offers_the_block() -> None:
    assert isinstance(body_field().stream_block.child_blocks["accordion"], AccordionBlock)
