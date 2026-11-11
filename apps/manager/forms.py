"""Manager forms (CORE.9): thin wrappers that validate input; the services own the rules."""

from typing import Any

from django import forms
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from wagtail.admin.rich_text import DraftailRichTextArea
from wagtail.rich_text import features as feature_registry

from apps.ingest import engine as ingest_engine
from apps.orders import engine as orders_engine
from apps.vendors.engine import LOGO_ALT_MAX_LENGTH, PROFILE_MAX_LENGTH

# Wagtail's default editor features (D-34), minus the ones whose chooser the Manager does not
# serve yet. Vendor members have no access to the Wagtail admin (`VendorAdminRefusalBackend`):
# `image` uses the Manager chooser (VEN.31); `embed` (media) and `link` (page chooser) would only
# fail for them, and `document-link` waits for VEN.31 part 2. The service refuses any image or
# document outside the vendor's own collection and any page link, so crafted HTML is no way around.
CHOOSER_FEATURES = frozenset({"embed", "document-link", "link"})
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


ALL_PERIODS = "all"


def _window_choices() -> list[tuple[int, str]]:
    return [
        (m, ngettext("%(count)d month", "%(count)d months", m) % {"count": m})
        for m in ingest_engine.LOGIN_WINDOWS
    ]


class MigrationChoiceForm(forms.Form):
    """What to migrate to the new platform (MIG.16). The engine and the service re-validate."""

    period = forms.ChoiceField(
        label=_("Period of orders to keep active"),
        choices=[
            *(
                (str(m), _("Last %(count)d months") % {"count": m})
                for m in ingest_engine.PERIOD_CHOICES
            ),
            (ALL_PERIODS, _("All orders")),
        ],
        widget=forms.RadioSelect,
        help_text=_("Older orders are kept for the legal retention period, but not active."),
    )
    data_types = forms.MultipleChoiceField(
        label=_("Data to migrate"),
        choices=[
            (ingest_engine.DATA_ORDERS, _("Orders")),
            (ingest_engine.DATA_CUSTOMERS, _("The customers of those orders")),
            (ingest_engine.DATA_PRODUCTS, _("Products")),
            (ingest_engine.DATA_REVIEWS, _("Product reviews")),
        ],
        widget=forms.CheckboxSelectMultiple,
        required=False,
        help_text=_("A customer with no order in the chosen period is not migrated."),
    )
    include_unpublished = forms.BooleanField(
        label=_("Bring drafts and archived products along, as hidden products"),
        required=False,
    )
    login_window_months = forms.TypedChoiceField(
        label=_("Customers who logged in within"),
        choices=_window_choices,
        coerce=int,
        help_text=_(
            "A customer account is migrated when it has an order in the last 24 months or a "
            "login within this window."
        ),
    )

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        if not self.errors:
            for name, message in ingest_engine.validate_choice(self.to_values()).items():
                self.add_error(name if name in self.fields else None, message)
        return data

    def to_values(self) -> ingest_engine.ChoiceValues:
        data = self.cleaned_data
        period = data["period"]
        return ingest_engine.ChoiceValues(
            period_months=None if period == ALL_PERIODS else int(period),
            data_types=ingest_engine.normalise_data_types(data["data_types"]),
            include_unpublished=data["include_unpublished"],
            login_window_months=data["login_window_months"],
        )

    @classmethod
    def initial_for(cls, values: ingest_engine.ChoiceValues) -> dict[str, object]:
        return {
            "period": ALL_PERIODS if values.period_months is None else str(values.period_months),
            "data_types": list(values.data_types),
            "include_unpublished": values.include_unpublished,
            "login_window_months": values.login_window_months,
        }
