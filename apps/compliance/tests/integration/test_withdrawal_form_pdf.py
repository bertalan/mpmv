"""I18N.20: the model withdrawal form as a downloadable PDF in each language."""

import io

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import translation
from pypdf import PdfReader

from apps.compliance.documents import render_model_withdrawal_form_pdf
from apps.core.i18n import configured_languages

# The form's key verbatim phrase in each language (the first words of the withdrawal sentence).
SENTENCE = (
    "I/We (*) hereby give notice that I/We (*) withdraw from my/our (*) contract of sale of the "
    "following goods (*)"
)
PHRASES = {
    "en": "hereby give notice",
    "it": "Con la presente",
    "de": "Hiermit widerrufe(n) ich/wir",
    "fr": "par la présente ma/notre (*) rétractation",
    "es": "Por la presente le comunico/comunicamos",
}


def _text(pdf: bytes) -> str:
    return " ".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)


def _flat(text: str) -> str:
    return "".join(text.split())


@pytest.mark.parametrize("lang", sorted(PHRASES))
def test_the_pdf_carries_the_forms_translated_text(lang: str) -> None:
    with translation.override(lang):
        from django.utils.translation import gettext

        expected = gettext("Model withdrawal form")
        sentence = gettext(SENTENCE)
    pdf = render_model_withdrawal_form_pdf(lang)
    text = _flat(_text(pdf))
    assert pdf.startswith(b"%PDF")
    assert _flat(expected) in text
    assert _flat(sentence) in text
    assert _flat(PHRASES[lang]) in text
    if lang == "de":
        assert "Datum" in text


def test_the_pdf_has_no_download_link_and_no_order_data() -> None:
    text = _flat(_text(render_model_withdrawal_form_pdf("en")))
    assert "Downloadtheform" not in text
    for marker in ("MPMV-", "@", "EUR"):
        assert marker not in text


@pytest.mark.django_db
def test_the_route_serves_a_cacheable_pdf_with_a_filename() -> None:
    lang = configured_languages()[0]
    with translation.override(lang):
        url = reverse("withdrawal_form_pdf")
    response = Client().get(url)
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"] == f'attachment; filename="withdrawal-form-{lang}.pdf"'
    assert "public" in response["Cache-Control"]
    assert _flat(PHRASES[lang]) in _flat(_text(response.content))


@pytest.mark.django_db
def test_the_consumer_information_page_links_to_the_pdf() -> None:
    with translation.override(configured_languages()[0]):
        page = reverse("consumer_information")
        link = reverse("withdrawal_form_pdf")
    body = Client().get(page).content.decode()
    assert f'href="{link}"' in body
