"""Manager forms (CORE.9): thin wrappers that validate input; the services own the rules."""

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.vendors.engine import LOGO_ALT_MAX_LENGTH, PROFILE_MAX_LENGTH


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


class VendorLogoForm(forms.Form):
    """The logo: a new image (checked by content in the service), its description, or removal."""

    logo = forms.FileField(
        label=_("Logo image"),
        required=False,
        widget=forms.ClearableFileInput(attrs={"accept": "image/png,image/jpeg,image/webp"}),
        help_text=_("PNG, JPEG or WebP. Leave empty to keep the current image."),
    )
    logo_alt = forms.CharField(
        label=_("Description of the logo"),
        required=False,
        max_length=LOGO_ALT_MAX_LENGTH,
        help_text=_("Plain text for people who cannot see the image, for example the shop name."),
    )
    remove = forms.BooleanField(label=_("Remove the logo"), required=False)
