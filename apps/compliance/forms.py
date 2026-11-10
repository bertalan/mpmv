"""The public notice form (DSA art. 16, CMP.5 part 2). Labels and help text only: the legal text
around the form comes from the CMS legal pages. The rules are in `engine.notice_problems`."""

from typing import Any

from django import forms
from django.utils.translation import gettext_lazy as _

from . import engine
from .models import Notice

URL_MAX = 2000

_FIELD_ERRORS = {
    "explanation": _("Explain why the content is illegal."),
    "url": _("Enter the exact web address (http or https) of the content."),
    "notifier_name": _("Enter your name."),
    "notifier_email": _("Enter a valid email address."),
    "good_faith": _("Confirm the statement to send the report."),
}


class NoticeForm(forms.Form):
    target_kind = forms.ChoiceField(
        label=_("What do you report?"), choices=Notice.TargetKind.choices
    )
    url = forms.CharField(
        label=_("Exact location (web address)"),
        max_length=URL_MAX,
        widget=forms.URLInput(attrs={"autocomplete": "off", "inputmode": "url"}),
        help_text=_("The full address of the page that shows the content."),
    )
    explanation = forms.CharField(
        label=_("Explanation"),
        max_length=10000,
        widget=forms.Textarea(attrs={"rows": 6}),
        help_text=_("Why you consider the content illegal."),
    )
    category = forms.ChoiceField(
        label=_("Type of report"),
        choices=Notice.Category.choices,
        initial=Notice.Category.GENERAL,
        help_text=_("Reports of child sexual abuse material may be sent without your details."),
    )
    notifier_name = forms.CharField(
        label=_("Your name"),
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={"autocomplete": "name"}),
    )
    notifier_email = forms.CharField(
        label=_("Your email address"),
        max_length=254,
        required=False,
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
        help_text=_("We confirm receipt and tell you our decision at this address."),
    )
    good_faith = forms.BooleanField(
        label=_(
            "I am convinced in good faith that the information in this report is accurate "
            "and complete."
        ),
        required=False,
    )

    def clean(self) -> dict[str, Any]:
        data: dict[str, Any] = super().clean() or {}
        problems = engine.notice_problems(
            explanation=data.get("explanation", ""),
            url=data.get("url", ""),
            category=data.get("category", ""),
            notifier_name=data.get("notifier_name", ""),
            notifier_email=data.get("notifier_email", ""),
            good_faith=bool(data.get("good_faith")),
        )
        for problem in problems:
            if problem.code not in self.errors:
                self.add_error(problem.code, _FIELD_ERRORS[problem.code])
        return data


class DecisionForm(forms.Form):
    """A reviewer's decision on a notice."""

    outcome = forms.ChoiceField(
        label=_("Decision"),
        choices=[
            (engine.NOTICE_ACTION_TAKEN, _("Action taken")),
            (engine.NOTICE_NO_ACTION, _("No action")),
        ],
    )
    reason = forms.CharField(
        label=_("Reason"),
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text=_("Sent to the notifier with the decision."),
    )
