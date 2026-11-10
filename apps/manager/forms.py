"""Manager forms (CORE.9): thin wrappers that validate input; the services own the rules."""

from typing import Any

from django import forms
from django.utils.translation import gettext_lazy as _
from wagtail.admin.rich_text import DraftailRichTextArea
from wagtail.rich_text import features as feature_registry

from apps.orders import engine as orders_engine
from apps.vendors.engine import LOGO_ALT_MAX_LENGTH, PROFILE_MAX_LENGTH

# Wagtail's default editor features (D-34), minus the three that open Wagtail's admin choosers.
# Vendor members have no access to the Wagtail admin (`VendorAdminRefusalBackend`), so those
# choosers would only fail for them; the service still refuses any image or document outside the
# vendor's own collection, so crafted HTML is not a way around it.
CHOOSER_FEATURES = frozenset({"image", "embed", "document-link"})
MANAGER_RICH_TEXT_FEATURES = [
    name for name in feature_registry.get_default_features() if name not in CHOOSER_FEATURES
]


class _Editor(DraftailRichTextArea):  # type: ignore[misc]  # Wagtail is untyped
    """Draftail posts its content state as JSON. A post that is not (a script, a hostile
    client) must not be a server error: it is taken as the HTML text it is, and cleaned like any
    other value by the service."""

    def value_from_datadict(self, data: Any, files: Any, name: str) -> Any:
        try:
            return super().value_from_datadict(data, files, name)
        except (ValueError, TypeError, KeyError, AttributeError):
            return data.get(name)


def _editor() -> DraftailRichTextArea:
    return _Editor(features=MANAGER_RICH_TEXT_FEATURES)


class StorefrontProfileForm(forms.Form):
    """The texts and policies of one language (the owner only, `shop.profile`).

    The tagline is plain text. The description and the policies are rich text (D-34): Wagtail's
    own editor, cleaned by the CMS.12 allow-list when saved and again when shown.
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
        widget=_editor(),
    )
    shipping_policy = forms.CharField(
        label=_("Shipping policy"),
        required=False,
        max_length=PROFILE_MAX_LENGTH["shipping_policy"],
        widget=_editor(),
    )
    refund_policy = forms.CharField(
        label=_("Returns and refunds policy"),
        required=False,
        max_length=PROFILE_MAX_LENGTH["refund_policy"],
        widget=_editor(),
        help_text=_(
            "Customers see this text. A change is live at once and the moderators are told."
        ),
    )
    cancellation_policy = forms.CharField(
        label=_("Cancellation policy"),
        required=False,
        max_length=PROFILE_MAX_LENGTH["cancellation_policy"],
        widget=_editor(),
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


class ShipmentForm(forms.Form):
    """The tracking number (and carrier) to ship with (ORD.6); the service checks them again."""

    tracking_number = forms.CharField(
        label=_("Tracking number"),
        max_length=orders_engine.TRACKING_MAX,
        help_text=_("Letters, digits, spaces and . / _ - only."),
    )
    carrier = forms.CharField(
        label=_("Carrier"), required=False, max_length=orders_engine.CARRIER_MAX
    )

    def clean_tracking_number(self) -> str:
        try:
            return orders_engine.normalise_tracking(self.cleaned_data["tracking_number"])
        except orders_engine.InvalidTracking:
            raise forms.ValidationError(
                _("Enter the tracking number: letters, digits, spaces and . / _ - only."),
                code="invalid",
            ) from None

    def clean_carrier(self) -> str:
        try:
            return orders_engine.normalise_carrier(self.cleaned_data["carrier"])
        except orders_engine.InvalidTracking:
            raise forms.ValidationError(_("Enter a valid carrier name."), code="invalid") from None
