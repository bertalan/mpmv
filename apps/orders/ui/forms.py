"""The checkout details form (ORD.5): who buys, where the goods go, and for a business, the
company and VAT id (checked against VIES by the view, TAX.5).

Every field has a length cap, an `autocomplete` token (WCAG 2.2 SC 1.3.5, 3.3.8) and a help text.
Business fields are required only when "I am buying for a business" is ticked; the theme script
shows them with that checkbox, and without JavaScript they are simply always visible.
"""

from typing import Any

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.tax import engine as tax_engine
from apps.vendors.forms import COUNTRY_NAMES

MAX_BODY_BYTES = 16 * 1024


def _autocomplete(token: str) -> dict[str, str]:
    return {"autocomplete": token}


class CheckoutDetailsForm(forms.Form):
    email = forms.EmailField(
        label=_("Email address"),
        help_text=_(
            "We send the order confirmation to this address. Required: without it we cannot "
            "confirm your order."
        ),
        max_length=254,
        widget=forms.EmailInput(attrs=_autocomplete("email")),
    )
    full_name = forms.CharField(
        label=_("Full name"),
        help_text=_("The name of the person who receives the parcel. Required for the carrier."),
        max_length=150,
        widget=forms.TextInput(attrs=_autocomplete("name")),
    )
    line1 = forms.CharField(
        label=_("Address"),
        help_text=_("Street and house number. Required: the carrier needs it to deliver."),
        max_length=200,
        widget=forms.TextInput(attrs=_autocomplete("address-line1")),
    )
    line2 = forms.CharField(
        label=_("Address, second line"),
        help_text=_("Apartment, floor or other detail. Optional."),
        required=False,
        max_length=200,
        widget=forms.TextInput(attrs=_autocomplete("address-line2")),
    )
    postcode = forms.CharField(
        label=_("Postcode"),
        help_text=_("The postal code of the delivery address. Required: it decides the shipping."),
        max_length=20,
        widget=forms.TextInput(attrs=_autocomplete("postal-code")),
    )
    city = forms.CharField(
        label=_("City"),
        help_text=_("The town or city of the delivery address. Required."),
        max_length=100,
        widget=forms.TextInput(attrs=_autocomplete("address-level2")),
    )
    state = forms.CharField(
        label=_("Province or state"),
        help_text=_("The province or state, where your country uses one. Optional."),
        required=False,
        max_length=100,
        widget=forms.TextInput(attrs=_autocomplete("address-level1")),
    )
    country = forms.ChoiceField(
        label=_("Country"),
        help_text=_("The country where we deliver. Required: shipping and VAT depend on it."),
        widget=forms.Select(attrs=_autocomplete("country")),
    )
    is_business = forms.BooleanField(
        label=_("I am buying for a business"),
        help_text=_(
            "Tick this to enter your company and VAT number. A valid EU VAT number can "
            "remove the VAT on a cross-border purchase."
        ),
        required=False,
    )
    company = forms.CharField(
        label=_("Company name"),
        help_text=_("The legal name of your business. Required when you buy for a business."),
        required=False,
        max_length=150,
        widget=forms.TextInput(attrs=_autocomplete("organization")),
    )
    vat_id = forms.CharField(
        label=_("VAT number"),
        help_text=_(
            "Your VAT number with the country prefix, for example DE123456789. We check it "
            "with the EU VIES service. Required when you buy for a business."
        ),
        required=False,
        max_length=20,
        widget=forms.TextInput(attrs=_autocomplete("off")),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        names = sorted(COUNTRY_NAMES.items(), key=lambda item: item[1])
        self.fields["country"].choices = [("", "---------"), *names]  # type: ignore[attr-defined]

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        if data.get("is_business"):
            for name in ("company", "vat_id"):
                if not str(data.get(name) or "").strip():
                    self.add_error(
                        name, _("Enter this when you buy for a business, or untick the box.")
                    )
            vat_id = str(data.get("vat_id") or "").strip()
            if vat_id and "vat_id" not in self.errors:
                try:
                    country = vat_country(vat_id, str(data.get("country") or ""))
                    tax_engine.vies_query(country, vat_id)
                except tax_engine.NotCheckable:
                    self.add_error(
                        "vat_id",
                        _(
                            "This does not look like an EU VAT number. Check the prefix and "
                            "the digits."
                        ),
                    )
        else:
            data["company"] = ""
            data["vat_id"] = ""
        return data

    def address(self) -> dict[str, str]:
        """The delivery address as `build_checkout` reads it."""
        data = self.cleaned_data
        return {
            "name": data["full_name"],
            "company": data.get("company", ""),
            "line1": data["line1"],
            "line2": data.get("line2", ""),
            "postcode": data["postcode"],
            "city": data["city"],
            "state": data.get("state", ""),
            "country": data["country"],
        }


def vat_country(vat_id: str, address_country: str) -> str:
    """The country of a VAT number: its two-letter prefix, else the delivery country."""
    head = vat_id.strip()[:2].upper()
    if len(head) == 2 and head.isascii() and head.isalpha():
        return {"EL": "GR"}.get(head, head)
    return address_country
