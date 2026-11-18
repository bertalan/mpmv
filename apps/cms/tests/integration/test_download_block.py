"""CMS.15 (D-36): the `download` block, a link to a media-library file with its type and size in
text before the click. Both themes; a missing file hides the block; the link names file and type."""

import re
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.test.utils import override_settings
from django.utils import translation
from wagtail import blocks
from wagtail.documents import get_document_model
from wagtail.documents.blocks import DocumentChooserBlock

from apps.cms.blocks import DownloadBlock
from apps.cms.models import BlogPostPage, HomePage, StandardPage, body_field

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("english_interface")]

PDF_BYTES = b"%PDF-1.4\n" + b"0" * (1_200_000 - 9)


def make_document(title: str = "Terms of sale", name: str = "terms.pdf") -> Any:
    doc = get_document_model()(title=title)
    doc.file.save(name, ContentFile(PDF_BYTES), save=False)
    doc.save()
    return doc


def value(doc: Any, title: str = "") -> Any:
    return DownloadBlock().to_python({"document": doc.pk if doc else None, "title": title})


def render(theme: str, val: Any, language: str = "en") -> str:
    with override_settings(MPMV_THEME_DEFAULT=theme), translation.override(language):
        return str(DownloadBlock().render(val, context={})).strip()


def test_the_block_chooses_a_document_and_has_an_optional_title() -> None:
    children = DownloadBlock().child_blocks
    assert isinstance(children["document"], DocumentChooserBlock)
    assert children["document"].required
    assert isinstance(children["title"], blocks.CharBlock)
    assert not children["title"].required


def test_the_title_may_be_longer_than_nothing_but_a_document_is_required() -> None:
    block = DownloadBlock()
    with pytest.raises(ValidationError):
        block.clean(block.to_python({"document": None, "title": "x"}))


@pytest.mark.parametrize("theme", ["base", "ride"])
def test_the_block_shows_the_file_type_and_size_computed_from_the_file(theme: str) -> None:
    doc = make_document()
    html = render(theme, value(doc))
    assert "PDF, 1.2 MB" in html
    assert doc.url in html


@pytest.mark.parametrize("theme", ["base", "ride"])
def test_the_title_defaults_to_the_document_title(theme: str) -> None:
    doc = make_document(title="Terms of sale")
    assert "Terms of sale" in render(theme, value(doc))
    assert "Price list" in render(theme, value(doc, title="Price list"))
    assert "Terms of sale" not in render(theme, value(doc, title="Price list"))


@pytest.mark.parametrize("theme", ["base", "ride"])
def test_the_link_text_names_the_file_and_its_type(theme: str) -> None:
    html = render(theme, value(make_document(title="Terms of sale")))
    link = re.search(r"<a\b[^>]*>(.*?)</a>", html, re.S)
    assert link is not None
    text = " ".join(re.sub(r"<[^>]+>", " ", link.group(1)).split())
    assert "Terms of sale" in text
    assert "PDF" in text
    assert "1.2 MB" in text
    assert html.count("<a ") == 1


@pytest.mark.parametrize("theme", ["base", "ride"])
def test_a_deleted_document_hides_the_block(theme: str) -> None:
    doc = make_document()
    pk = doc.pk
    doc.delete()
    assert render(theme, DownloadBlock().to_python({"document": pk, "title": "x"})) == ""


@pytest.mark.parametrize("theme", ["base", "ride"])
def test_a_document_whose_file_is_gone_hides_the_block(theme: str) -> None:
    doc = make_document()
    doc.file.storage.delete(doc.file.name)
    html = render(theme, value(doc))
    assert html == ""


@pytest.mark.parametrize("theme", ["base", "ride"])
def test_the_size_uses_the_decimal_separator_of_the_page_language(theme: str) -> None:
    html = render(theme, value(make_document()), language="it")
    assert "PDF, 1,2 MB" in html


@pytest.mark.parametrize("model", [HomePage, StandardPage, BlogPostPage])
def test_the_page_bodies_offer_the_block(model: Any) -> None:
    assert "download" in model._meta.get_field("body").stream_block.child_blocks


def test_the_shared_body_field_offers_the_block() -> None:
    assert isinstance(body_field().stream_block.child_blocks["download"], DownloadBlock)
