"""CMS.1: a menu link is a page (with an optional label) or an external URL with a label."""

import pytest
from django.core.exceptions import ValidationError

from apps.cms.models import ExternalLinkBlock, PageLinkBlock


def test_a_page_link_needs_a_page_but_not_a_label() -> None:
    block = PageLinkBlock()
    with pytest.raises(ValidationError):
        block.clean(block.to_python({"page": None, "label": "About"}))


@pytest.mark.parametrize(
    "value",
    [
        {"url": "", "label": "Help"},
        {"url": "https://example.com/help/", "label": ""},
        {"url": "javascript:alert(1)", "label": "Help"},
    ],
)
def test_an_external_link_needs_a_valid_url_and_a_label(value: dict[str, str]) -> None:
    block = ExternalLinkBlock()
    with pytest.raises(ValidationError):
        block.clean(block.to_python(value))


def test_a_complete_external_link_is_valid() -> None:
    block = ExternalLinkBlock()
    value = block.clean(block.to_python({"url": "https://example.com/help/", "label": "Help"}))
    assert value["url"] == "https://example.com/help/"
