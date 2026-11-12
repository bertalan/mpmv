"""The public notice form (DSA art. 16, CMP.5 part 2). Labels and help text only: the legal text
around the form comes from the CMS legal pages. The rules are in `engine.notice_problems`."""

from typing import Any

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
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
        label=_("What do you report?"),
        choices=Notice.TargetKind.choices,
        help_text=_(
            "Choose what you are reporting: a product, an event, a vendor shop or other "
            "content of the site."
        ),
    )
    url = forms.CharField(
        label=_("Exact location (web address)"),
        max_length=URL_MAX,
        widget=forms.URLInput(attrs={"autocomplete": "off", "inputmode": "url"}),
        help_text=_(
            "The full web address of the page that shows the content, starting with http://"
            " or https://. Required."
        ),
    )
    explanation = forms.CharField(
        label=_("Explanation"),
        max_length=10000,
        widget=forms.Textarea(attrs={"rows": 6}),
        help_text=_("Tell us why you think the content is illegal. Required."),
    )
    category = forms.ChoiceField(
        label=_("Type of report"),
        choices=Notice.Category.choices,
        initial=Notice.Category.GENERAL,
        help_text=_(
            "Choose 'Illegal content' for most reports. A report of child sexual abuse "
            "material can be sent without your name and email address."
        ),
    )
    notifier_name = forms.CharField(
        label=_("Your name"),
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={"autocomplete": "name"}),
        help_text=_("Your name. Required, except for a report of child sexual abuse material."),
    )
    notifier_email = forms.CharField(
        label=_("Your email address"),
        max_length=254,
        required=False,
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
        help_text=_(
            "Your email address. We confirm that we received the report and tell you our "
            "decision there. Required, except for a report of child sexual abuse material."
        ),
    )
    good_faith = forms.BooleanField(
        label=_(
            "I am convinced in good faith that the information in this report is accurate "
            "and complete."
        ),
        required=False,
        help_text=_("Tick the box to confirm the statement. Without it the report cannot be sent."),
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
        email = data.get("notifier_email", "").strip()
        if email and "notifier_email" not in self.errors:
            try:
                validate_email(email)
            except ValidationError:
                self.add_error("notifier_email", _FIELD_ERRORS["notifier_email"])
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
        help_text=_(
            "Choose 'Action taken' if the content was restricted, or 'No action' if it was "
            "left as it is."
        ),
    )
    reason = forms.CharField(
        label=_("Reason"),
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text=_(
            "Why you decided this way. The notifier receives this text with the decision. Required."
        ),
    )


class RestrictionForm(forms.Form):
    """A reviewer's action on content, with the statement of reasons (DSA art. 17)."""

    target_kind = forms.ChoiceField(
        label=_("What is restricted"),
        choices=[
            ("product", _("Product (unpublish)")),
            ("event", _("Event (hide)")),
            ("vendor", _("Vendor storefront (suspend)")),
        ],
        help_text=_(
            "What you restrict: a product is unpublished, an event is hidden, a vendor shop"
            " is suspended."
        ),
    )
    target_ref = forms.UUIDField(
        label=_("Reference of the content"),
        help_text=_(
            "The public id of the content, as shown in the Manager (a long code with "
            "letters, numbers and dashes)."
        ),
    )
    scope = forms.CharField(
        label=_("Scope"),
        help_text=_(
            "What is restricted, where and for how long, in plain words. The vendor sees this text."
        ),
    )
    valid_until = forms.DateField(
        label=_("Restriction ends on"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text=_(
            "The last day of the restriction. Leave it empty when the restriction has no end date."
        ),
    )
    facts = forms.CharField(
        label=_("Facts and circumstances relied on"),
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text=_(
            "The facts and circumstances you relied on to decide. The vendor sees this text."
        ),
    )
    transparency_explanation = forms.CharField(
        label=_("Explanation for the public database"),
        max_length=engine.PUBLIC_TEXT_LIMIT,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_(
            "Warning: this text is meant for the EU transparency database (the submission is "
            "not built yet, so for now it is only stored). At most 2000 characters. Write no "
            "personal data, no names, no email addresses or web addresses."
        ),
    )
    ground_kind = forms.ChoiceField(
        label=_("Ground"),
        choices=[
            (engine.GROUND_ILLEGAL, _("The content is illegal")),
            (engine.GROUND_TERMS, _("The content is incompatible with the platform terms")),
        ],
        help_text=_("Say whether the content is illegal or only against the platform terms."),
    )
    ground = forms.CharField(
        label=_("Legal provision or term relied on"),
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text=_(
            "The law or the term of the platform terms you relied on, for example the "
            "article and the name of the law."
        ),
    )
    automated_detection = forms.BooleanField(
        label=_("Automated means were used to detect the content"),
        required=False,
        help_text=_("Tick it if software helped to find the content."),
    )
    automated_decision = forms.BooleanField(
        label=_("Automated means were used to decide"),
        required=False,
        help_text=_("Tick it if software took the decision."),
    )


class AuthoritiesForm(forms.Form):
    """Article 18: staff told the authorities of a suspected threat to life or safety."""

    authority = forms.CharField(
        label=_("Authority told"),
        max_length=255,
        help_text=_(
            "The name of the authority you told about a suspected threat to life or safety."
            " Required."
        ),
    )
    note = forms.CharField(
        label=_("Note"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_(
            "An optional note. It is kept with the notice and is not written in the audit log."
        ),
    )
