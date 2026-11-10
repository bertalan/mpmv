"""Manager forms (CORE.9): thin wrappers that validate input; the services own the rules."""

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.vendors.engine import PROFILE_MAX_LENGTH


class StorefrontProfileForm(forms.Form):
    """The texts and policies of one language (the owner only, `shop.profile`).

    Plain text only: HTML is refused by the service and every page escapes these texts.
    """

    tagline = forms.CharField(
        label=_("Tagline"),
        required=False,
        max_length=PROFILE_MAX_LENGTH["tagline"],
        help_text=_("One line under the shop name. Plain text."),
    )
    description = forms.CharField(
        label=_("About the shop"),
        required=False,
        max_length=PROFILE_MAX_LENGTH["description"],
        widget=forms.Textarea(attrs={"rows": 6}),
        help_text=_("Plain text, no HTML."),
    )
    shipping_policy = forms.CharField(
        label=_("Shipping policy"),
        required=False,
        max_length=PROFILE_MAX_LENGTH["shipping_policy"],
        widget=forms.Textarea(attrs={"rows": 5}),
    )
    refund_policy = forms.CharField(
        label=_("Returns and refunds policy"),
        required=False,
        max_length=PROFILE_MAX_LENGTH["refund_policy"],
        widget=forms.Textarea(attrs={"rows": 5}),
        help_text=_(
            "Customers see this text. A change is live at once and the moderators are told."
        ),
    )
    cancellation_policy = forms.CharField(
        label=_("Cancellation policy"),
        required=False,
        max_length=PROFILE_MAX_LENGTH["cancellation_policy"],
        widget=forms.Textarea(attrs={"rows": 5}),
    )
