"""Forms that store free text in Unicode NFC (I18N.10).

Kept apart from `apps.core.forms` (which imports the core models) so a model can name
`NFCModelForm` as its `base_form_class` at import time.
"""

from typing import Any

from django import forms
from django.db.models import Model
from wagtail import blocks
from wagtail.admin.forms import WagtailAdminModelForm, WagtailAdminPageForm
from wagtail.admin.panels import InlinePanel
from wagtail.blocks import StreamValue

from .text import nfc

# Form fields whose value is a URL: kept byte-exact.
EXACT_FIELDS = (forms.URLField,)
TEXT_BLOCKS = (blocks.CharBlock, blocks.TextBlock, blocks.RichTextBlock)


def nfc_block_data(block: Any, raw: Any) -> Any:
    """`raw` (the JSON form of a value of `block`) with its text blocks in NFC.

    Only char, text and rich text blocks are touched: URLs, choices, ids and chooser values stay
    exact. A rich text keeps its markup (NFC leaves ASCII untouched).
    """
    if isinstance(block, blocks.StreamBlock) and isinstance(raw, list):
        return [
            {**item, "value": nfc_block_data(block.child_blocks[item["type"]], item.get("value"))}
            if isinstance(item, dict) and item.get("type") in block.child_blocks
            else item
            for item in raw
        ]
    if isinstance(block, blocks.StructBlock) and isinstance(raw, dict):
        return {
            key: nfc_block_data(block.child_blocks[key], item)
            if key in block.child_blocks
            else item
            for key, item in raw.items()
        }
    if isinstance(block, blocks.ListBlock) and isinstance(raw, list):
        return [
            {**item, "value": nfc_block_data(block.child_block, item.get("value"))}
            if isinstance(item, dict) and "value" in item
            else nfc_block_data(block.child_block, item)
            for item in raw
        ]
    if isinstance(block, TEXT_BLOCKS) and isinstance(raw, str):
        return nfc(raw)
    return raw


def nfc_stream_value(value: StreamValue) -> StreamValue:
    """`value` (a StreamField's cleaned value) with its text blocks in NFC; itself if unchanged."""
    raw = value.stream_block.get_prep_value(value)
    fixed = nfc_block_data(value.stream_block, raw)
    return value if fixed == raw else value.stream_block.to_python(fixed)


class NFCFormMixin:
    """Every string in the cleaned data is put in NFC; other values are kept.

    The text is normalised in `_clean_fields`, after each field's own clean and before the form's
    `clean`: Wagtail's slug and sibling checks (and any later `clean`) then see NFC values.
    URLs stay exact; slugs are normalised so a decomposed slug clashes with its composed twin.
    Passwords (a `PasswordInput` widget) and the names in `nfc_exact_fields` (emails, VAT ids,
    codes matched byte for byte) are kept exactly as typed.
    """

    nfc_exact_fields: tuple[str, ...] = ()

    def _keeps_exact(self, field: Any, name: str) -> bool:
        if name in self.nfc_exact_fields or isinstance(field, EXACT_FIELDS):
            return True
        return isinstance(getattr(field, "widget", None), forms.PasswordInput)

    def _clean_fields(self) -> None:
        super()._clean_fields()  # type: ignore[misc]
        cleaned: dict[str, Any] = self.cleaned_data  # type: ignore[attr-defined]
        fields = getattr(self, "fields", {})
        for name, value in list(cleaned.items()):
            if isinstance(value, str):
                if not self._keeps_exact(fields.get(name), name):
                    cleaned[name] = nfc(value)
            elif isinstance(value, StreamValue):
                cleaned[name] = nfc_stream_value(value)  # StreamField text blocks, one hook


class NFCForm(NFCFormMixin, forms.Form):
    """A plain form (for example a Manager staff form) that stores its text in NFC."""


class NFCAdminForm[M: Model](NFCFormMixin, forms.ModelForm[M]):
    """The Django admin model form (a `ModelAdmin.form`) that stores its text in NFC."""


class NFCModelForm(NFCFormMixin, WagtailAdminModelForm):  # type: ignore[misc]  # Wagtail is untyped
    """The Wagtail model form (a snippet's `base_form_class`) that stores its text in NFC."""


class NFCPageForm(NFCFormMixin, WagtailAdminPageForm):  # type: ignore[misc]  # Wagtail is untyped
    """The Wagtail page form (a page's `base_form_class`) that stores its text in NFC."""


class NFCInlinePanel(InlinePanel):  # type: ignore[misc]  # Wagtail is untyped
    """An `InlinePanel` whose child forms (one per row) store their text in NFC.

    Wagtail builds the child forms from the child model, not from the parent form's base class.
    """

    def get_form_options(self) -> dict[str, Any]:
        options: dict[str, Any] = super().get_form_options()
        for formset in options["formsets"].values():
            form = formset.get("form")
            if form is None:
                formset["form"] = NFCModelForm
            elif not issubclass(form, NFCFormMixin):
                formset["form"] = type(form)(form.__name__, (NFCFormMixin, form), {})
        return options
