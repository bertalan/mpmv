"""The edit form of the footer settings (CMS.17): at most four widget columns per language."""

from django.utils.translation import gettext as _
from wagtail.admin.forms import WagtailAdminModelForm

from apps.core.nfc_forms import NFCFormMixin

from . import engine


class FooterSettingsForm(NFCFormMixin, WagtailAdminModelForm):  # type: ignore[misc]  # Wagtail is untyped
    def is_valid(self) -> bool:
        """The column formset is validated after the form's `clean`, so the count is checked
        once both are valid."""
        valid: bool = super().is_valid()
        formset = self.formsets.get("columns")
        if valid and formset is not None:
            languages = [
                form.cleaned_data["language"]
                for form in formset.forms
                if getattr(form, "cleaned_data", None)
                and not form.cleaned_data.get("DELETE")
                and form.cleaned_data.get("language")
            ]
            for code in engine.too_many_columns(languages):
                self.add_error(
                    None,
                    _("The footer shows at most four widget columns for each language (%(code)s).")
                    % {"code": code},
                )
                valid = False
        return valid
