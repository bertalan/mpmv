"""Manager forms (CORE.9): thin wrappers that validate input; the services own the rules."""

from django import forms
from django.utils.translation import gettext_lazy as _
from wagtail.admin.rich_text import DraftailRichTextArea
from wagtail.rich_text import features as feature_registry

from apps.vendors.engine import PROFILE_MAX_LENGTH

# Wagtail's default editor features (D-34), minus the three that open Wagtail's admin choosers.
# Vendor members have no access to the Wagtail admin (`VendorAdminRefusalBackend`), so those
# choosers would only fail for them; the service still refuses any image or document outside the
# vendor's own collection, so crafted HTML is not a way around it.
CHOOSER_FEATURES = frozenset({"image", "embed", "document-link"})
MANAGER_RICH_TEXT_FEATURES = [
    name for name in feature_registry.get_default_features() if name not in CHOOSER_FEATURES
]


def _editor() -> DraftailRichTextArea:
    return DraftailRichTextArea(features=MANAGER_RICH_TEXT_FEATURES)


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
