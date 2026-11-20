"""I18N.10: CMS page admin forms and StreamField blocks store text in Unicode NFC."""

import unicodedata
from typing import Any

import pytest
from django.forms.utils import ErrorDict
from wagtail.admin.panels import get_edit_handler

from apps.cms.models import (
    BlogAuthor,
    BlogIndexPage,
    BlogPostPage,
    FormPage,
    HomePage,
    LegalPage,
    StandardPage,
    TermLandingPage,
    VendorLandingPage,
    VendorRecruitmentPage,
)
from apps.cms.tests.factories import StandardPageFactory
from apps.core.nfc_forms import NFCFormMixin, NFCPageForm, nfc_block_data
from apps.events.models import EventIndexPage, EventPage

pytestmark = pytest.mark.django_db

COMPOSED = "Café à la carte"
DECOMPOSED = unicodedata.normalize("NFD", COMPOSED)
URL = "https://example.org/café?q=é"


@pytest.mark.parametrize(
    "model",
    [
        HomePage,
        StandardPage,
        LegalPage,
        VendorRecruitmentPage,
        BlogIndexPage,
        BlogPostPage,
        TermLandingPage,
        VendorLandingPage,
        FormPage,
        EventIndexPage,
        EventPage,
    ],
)
def test_every_page_type_has_the_nfc_form(model: Any) -> None:
    form_class = get_edit_handler(model).get_form_class()
    assert issubclass(form_class, NFCFormMixin)


def _clean(form_class: Any, page: Any, data: dict[str, Any]) -> dict[str, Any]:
    form = form_class(instance=page, parent_page=page.get_parent())
    form._errors = ErrorDict()
    form.cleaned_data = dict(data)
    out: dict[str, Any] = NFCFormMixin.clean(form)
    return out


def test_a_page_form_cleans_title_and_stream_text_to_nfc() -> None:
    page = StandardPageFactory.create()
    form_class = get_edit_handler(StandardPage).get_form_class()
    assert issubclass(form_class, NFCPageForm)
    body = StandardPage._meta.get_field("body").stream_block
    value = body.to_python(
        [
            {"type": "paragraph", "value": f"<p><b>{DECOMPOSED}</b></p>"},
            {"type": "cta", "value": {"heading": DECOMPOSED, "url": URL}}
            if "cta" in body.child_blocks and "url" in body.child_blocks["cta"].child_blocks
            else {"type": "paragraph", "value": "<p>x</p>"},
        ]
    )
    cleaned = _clean(form_class, page, {"title": DECOMPOSED, "body": value})
    assert cleaned["title"] == COMPOSED
    raw = body.get_prep_value(cleaned["body"])
    assert raw[0]["value"] == f"<p><b>{COMPOSED}</b></p>"


def test_a_url_and_ids_in_a_block_stay_byte_exact() -> None:
    body = StandardPage._meta.get_field("body").stream_block
    cta = body.child_blocks["cta"]
    link = next(
        (k for k, b in cta.child_blocks.items() if b.__class__.__name__ == "URLBlock"), None
    )
    raw = [{"type": "cta", "id": "abc", "value": {"heading": DECOMPOSED, link or "x": URL}}]
    out = nfc_block_data(body, raw)
    assert out[0]["id"] == "abc"
    assert out[0]["value"]["heading"] == COMPOSED
    if link:
        assert out[0]["value"][link] == URL


def test_unchanged_stream_value_is_returned_as_is() -> None:
    from apps.core.nfc_forms import nfc_stream_value

    body = StandardPage._meta.get_field("body").stream_block
    value = body.to_python([{"type": "paragraph", "value": f"<p>{COMPOSED}</p>"}])
    assert nfc_stream_value(value) is value


def test_snippet_stream_fields_are_nfc() -> None:
    form_class = get_edit_handler(BlogAuthor).get_form_class()
    assert issubclass(form_class, NFCFormMixin)
