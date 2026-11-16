"""Forms of the fitment pages of the Manager (FIT.1 part 2). Validation of shape only: the rules
(limits, name clashes, orphaning) stay in `apps.fitment.services`."""

import uuid
from typing import Any

from django import forms
from django.conf import settings
from django.utils.translation import gettext_lazy as _

from apps.core import translations
from apps.fitment.models import FitmentNode, NodeStatus
from apps.fitment.services import LevelLabels


class SchemeForm(forms.Form):
    garage_noun_key = forms.CharField(
        label=_("Garage noun"),
        max_length=60,
        help_text=_(
            "The word shown to customers for their saved items, for example Garage or My devices."
        ),
    )
    year_enabled = forms.BooleanField(
        label=_("Uses a year"),
        required=False,
        help_text=_("Turn on when a year or year range means something in this sector."),
    )
    year_min = forms.IntegerField(label=_("Earliest year"), min_value=1000, max_value=2100)
    year_max_offset = forms.IntegerField(
        label=_("Latest year, years from now"),
        min_value=0,
        max_value=20,
        help_text=_("0 means this year."),
    )
    contains_special_category = forms.BooleanField(
        label=_("Items reveal sensitive information"),
        required=False,
        help_text=_(
            "Turn on only when what a customer saves could reveal sensitive personal data. The "
            "garage is then kept for the visit only and not saved to the account."
        ),
    )
    max_profiles = forms.IntegerField(label=_("Profiles per customer"), min_value=1, max_value=50)


def _language_names() -> list[tuple[str, str]]:
    return [(code, str(name)) for code, name in settings.LANGUAGES]


class LevelLabelsForm(forms.Form):
    """The label, plural label and picker hint of a level, one set per site language. The source
    language is required; another language is saved only when its label is filled."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        source = translations.source_language()
        for code, name in _language_names():
            required = code == source
            self.fields[f"label_{code}"] = forms.CharField(
                label=f"{_('Label')} ({name})", max_length=60, required=required
            )
            self.fields[f"plural_{code}"] = forms.CharField(
                label=f"{_('Plural label')} ({name})", max_length=60, required=required
            )
            self.fields[f"placeholder_{code}"] = forms.CharField(
                label=f"{_('Picker hint')} ({name})", max_length=80, required=False
            )

    def clean(self) -> dict[str, Any]:
        cleaned: dict[str, Any] = super().clean() or {}
        for code, _name in _language_names():
            label, plural = cleaned.get(f"label_{code}", ""), cleaned.get(f"plural_{code}", "")
            if bool(label) != bool(plural):
                self.add_error(
                    f"plural_{code}" if label else f"label_{code}",
                    _("Give both the label and the plural label."),
                )
        return cleaned

    def labels(self) -> dict[str, LevelLabels]:
        found: dict[str, LevelLabels] = {}
        for code, _name in _language_names():
            label = self.cleaned_data.get(f"label_{code}", "")
            if label:
                found[code] = LevelLabels(
                    label=label,
                    plural_label=self.cleaned_data[f"plural_{code}"],
                    placeholder=self.cleaned_data.get(f"placeholder_{code}", ""),
                )
        return found


class AddLevelForm(LevelLabelsForm):
    code = forms.SlugField(
        label=_("Code"),
        max_length=40,
        help_text=_("Lower case letters, digits, - and _. It cannot change later."),
    )
    translate_names = forms.BooleanField(
        label=_("Names differ by language"),
        required=False,
        help_text=_("Cannot change once the level has nodes."),
    )

    field_order = ["code", "translate_names"]


class NodeForm(forms.Form):
    name = forms.CharField(label=_("Name"), max_length=120)
    status = forms.ChoiceField(
        label=_("Status"),
        choices=[
            (NodeStatus.ACTIVE.value, _("Active")),
            (NodeStatus.PROPOSED.value, _("Proposed")),
        ],
        initial=NodeStatus.ACTIVE.value,
        help_text=_("A proposed node waits for review and is not offered in pickers."),
    )
    parent = forms.CharField(widget=forms.HiddenInput, required=False)

    def clean_parent(self) -> FitmentNode | None:
        value = self.cleaned_data.get("parent", "")
        if not value:
            return None
        try:
            node: FitmentNode = FitmentNode.objects.get(public_id=uuid.UUID(value))
            return node
        except (ValueError, FitmentNode.DoesNotExist):
            raise forms.ValidationError(_("This parent does not exist.")) from None


class NodeNameForm(forms.Form):
    name = forms.CharField(label=_("Name"), max_length=120)


class AliasForm(forms.Form):
    alias = forms.CharField(label=_("Alias"), max_length=120)
    language = forms.ChoiceField(
        label=_("Language"),
        required=False,
        choices=[("", _("Any language")), *_language_names()],
    )


class TranslatedNameForm(forms.Form):
    language = forms.ChoiceField(label=_("Language"), choices=_language_names)
    name = forms.CharField(label=_("Name"), max_length=120)


class ReasonForm(forms.Form):
    reason = forms.CharField(
        label=_("Reason"),
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("Kept in the audit log. A refusal removes the proposal."),
    )
