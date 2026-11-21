"""I18N.10: the tax admin forms store their text in NFC; codes stay as typed."""

import secrets
import unicodedata

import pytest
from django.forms import modelform_factory

from apps.tax.admin import ApproveForm, LegalNoteForm, RejectForm, TerritoryForm
from apps.tax.models import Territory, VendorTaxProfileTranslation
from apps.tax.tests.factories import VendorTaxProfileTranslationFactory

pytestmark = pytest.mark.django_db


def decomposed() -> str:
    """A generated text with an "e" and a combining acute accent."""
    return f"x{secrets.token_hex(4)}e\u0301"


def composed(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def test_the_review_reasons_are_stored_in_nfc() -> None:
    for form_class in (ApproveForm, RejectForm):
        reason = decomposed()
        form = form_class({"reason": reason})
        assert form.is_valid()
        assert form.cleaned_data["reason"] == composed(reason)


def test_the_territory_name_is_stored_in_nfc() -> None:
    name = decomposed()
    form_class = modelform_factory(
        Territory, form=TerritoryForm, fields=("code", "name", "country", "vat_status")
    )
    form = form_class(
        {"code": "XX-NF", "name": name, "country": "ES", "vat_status": Territory.VatStatus.SPECIAL}
    )
    assert form.is_valid(), form.errors
    assert form.save().name == composed(name)
    assert TerritoryForm.nfc_exact_fields == ("code",)


def test_the_legal_note_is_stored_in_nfc() -> None:
    note = VendorTaxProfileTranslationFactory.create()
    text = decomposed()
    form_class = modelform_factory(
        VendorTaxProfileTranslation, form=LegalNoteForm, fields=("legal_note",)
    )
    form = form_class({"legal_note": text}, instance=note)
    assert form.is_valid(), form.errors
    assert form.save().legal_note == composed(text)
