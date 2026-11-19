"""Forms that store free text in Unicode NFC (I18N.10).

Kept apart from `apps.core.forms` (which imports the core models) so a model can name
`NFCModelForm` as its `base_form_class` at import time.
"""

from typing import Any

from wagtail.admin.forms import WagtailAdminModelForm

from .text import nfc


class NFCFormMixin:
    """Every string in the cleaned data is put in NFC; other values are kept.

    Put it first in the bases (`class MyForm(NFCFormMixin, forms.Form)`): a `clean` that calls
    `super().clean()` then sees NFC values.
    """

    def clean(self) -> dict[str, Any]:
        cleaned: dict[str, Any] = super().clean() or self.cleaned_data  # type: ignore[misc,attr-defined]
        for name, value in list(cleaned.items()):
            if isinstance(value, str):
                cleaned[name] = nfc(value)
        return cleaned


class NFCModelForm(NFCFormMixin, WagtailAdminModelForm):  # type: ignore[misc]  # Wagtail is untyped
    """The Wagtail model form (a snippet's `base_form_class`) that stores its text in NFC."""
