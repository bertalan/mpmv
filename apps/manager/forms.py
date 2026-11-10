"""Manager forms (CORE.9): thin wrappers that validate input; the services own the rules."""

from typing import cast

from django import forms
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.vendors.models import VendorTranslation


def _limit(name: str) -> int | None:
    return cast("models.CharField[str, str]", VendorTranslation._meta.get_field(name)).max_length


class StorefrontProfileForm(forms.Form):
    """The texts and policies of one language (the owner only, `shop.profile`)."""

    tagline = forms.CharField(
        label=_("Tagline"),
        required=False,
        max_length=_limit("tagline"),
        help_text=_("One line under the shop name."),
    )
    description = forms.CharField(
        label=_("About the shop"), required=False, widget=forms.Textarea(attrs={"rows": 6})
    )
    shipping_policy = forms.CharField(
        label=_("Shipping policy"), required=False, widget=forms.Textarea(attrs={"rows": 5})
    )
    refund_policy = forms.CharField(
        label=_("Returns and refunds policy"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 5}),
        help_text=_(
            "Customers see this text. A change is live at once and the moderators are told."
        ),
    )
    cancellation_policy = forms.CharField(
        label=_("Cancellation policy"), required=False, widget=forms.Textarea(attrs={"rows": 5})
    )
