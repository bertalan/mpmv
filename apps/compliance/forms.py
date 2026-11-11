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
    )
    reason = forms.CharField(
        label=_("Reason"),
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text=_("Sent to the notifier with the decision."),
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
    )
    target_ref = forms.UUIDField(
        label=_("Reference of the content"), help_text=_("The public id shown in the Manager.")
    )
    scope = forms.CharField(
        label=_("Scope"), help_text=_("What is restricted, where and for how long.")
    )
    valid_until = forms.DateField(
        label=_("Restriction ends on"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text=_("Leave empty when the restriction has no end date."),
    )
    facts = forms.CharField(
        label=_("Facts and circumstances relied on"), widget=forms.Textarea(attrs={"rows": 4})
    )
    transparency_explanation = forms.CharField(
        label=_("Explanation for the public database"),
        max_length=engine.PUBLIC_TEXT_LIMIT,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_(
            "Warning: this text is published in the EU transparency database. Write no personal "
            "data, no names, no email addresses or web addresses."
        ),
    )
    ground_kind = forms.ChoiceField(
        label=_("Ground"),
        choices=[
            (engine.GROUND_ILLEGAL, _("The content is illegal")),
            (engine.GROUND_TERMS, _("The content is incompatible with the platform terms")),
        ],
    )
    ground = forms.CharField(
        label=_("Legal provision or term relied on"), widget=forms.Textarea(attrs={"rows": 2})
    )
    automated_detection = forms.BooleanField(
        label=_("Automated means were used to detect the content"), required=False
    )
    automated_decision = forms.BooleanField(
        label=_("Automated means were used to decide"), required=False
    )


class AuthoritiesForm(forms.Form):
    """Article 18: staff told the authorities of a suspected threat to life or safety."""

    authority = forms.CharField(label=_("Authority told"), max_length=255)
    note = forms.CharField(
        label=_("Note"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("Kept with the notice, not in the audit log."),
    )


class WithdrawalLookupForm(forms.Form):
    """Step one of the withdrawal function: which contract. Minimal data (CMP.6)."""

    order_number = forms.CharField(
        label=_("Order number"),
        max_length=32,
        widget=forms.TextInput(attrs={"autocomplete": "off"}),
        help_text=_("As in the confirmation email, for example 2027-000123."),
    )
    email = forms.EmailField(
        label=_("Email address of the order"),
        max_length=254,
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
    )
