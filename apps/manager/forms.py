"""Manager forms (CORE.9): thin wrappers that validate input; the services own the rules."""

from typing import Any

from django import forms
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _
from wagtail.admin.rich_text import DraftailRichTextArea
from wagtail.rich_text import features as feature_registry

from apps.orders import engine as orders_engine
from apps.vendors.engine import LOGO_ALT_MAX_LENGTH, PROFILE_MAX_LENGTH

# Wagtail's default editor features (D-34), minus the ones whose chooser the Manager does not
# serve. Vendor members have no access to the Wagtail admin (`VendorAdminRefusalBackend`):
# `image` and `link` use the Manager chooser and link dialog (VEN.31); `embed` (media) would only
# fail for them. `document-link` is off until ARC.15 (private storage with signed, expiring URLs):
# a vendor PDF would be public by URL (owner decision). The service refuses any image outside the
# vendor's own collection, any page or document link and any non-https link, so crafted HTML is
# no way around.
CHOOSER_FEATURES = frozenset({"embed", "document-link"})
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
    widget = _Editor(features=MANAGER_RICH_TEXT_FEATURES)
    # The image button opens the Manager chooser (VEN.31), not Wagtail's admin chooser.
    for entity in widget.options.get("entityTypes", []):
        if entity.get("type") == "IMAGE":
            entity["chooserUrls"] = {"imageChooser": reverse_lazy("manager:shop:image_choose")}
        elif entity.get("type") == "LINK":
            # The Manager dialog takes external links only: no page, email, phone or anchor chooser.
            link = reverse_lazy("manager:shop:link_choose")
            entity["chooserUrls"] = {"pageChooser": link, "externalLinkChooser": link}
    return widget


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
        help_text=_(
            "Your story, shown on your shop page in the language selected above. Up to about "
            "10,000 characters including formatting. Only the formatting the editor offers is kept."
        ),
    )
    shipping_policy = forms.CharField(
        label=_("Shipping policy"),
        required=False,
        max_length=PROFILE_MAX_LENGTH["shipping_policy"],
        widget=_editor(),
        help_text=_(
            "How and when you ship. Customers see it in the 'Shipping' section of your shop "
            "page. Leave it empty and the section is not shown. Up to about 5,000 characters "
            "including formatting."
        ),
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
        help_text=_(
            "When and how a customer can cancel an order. Customers see it in the 'Cancellation' "
            "section of your shop page. Leave it empty and the section is not shown. Up to about "
            "5,000 characters including formatting. If you are unsure what the law requires, ask a "
            "legal adviser."
        ),
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
    remove = forms.BooleanField(
        label=_("Remove the logo"),
        required=False,
        help_text=_(
            "Tick it and save to delete the current logo. A new image and the description "
            "above are then ignored."
        ),
    )


class ShipmentForm(forms.Form):
    """The tracking number (and carrier) to ship with (ORD.6); the service checks them again."""

    tracking_number = forms.CharField(
        label=_("Tracking number"),
        max_length=orders_engine.TRACKING_MAX,
        help_text=_("Letters, digits, spaces and . / _ - only."),
    )
    carrier = forms.CharField(
        label=_("Carrier"),
        required=False,
        max_length=orders_engine.CARRIER_MAX,
        help_text=_(
            "Optional. The name of the courier, for example the one on the parcel. The customer "
            "gets it in the shipping email together with the tracking number."
        ),
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
