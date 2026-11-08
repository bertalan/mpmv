"""Sample data for the staff-only styleguide page (THM.4, spec 14 §14.9).

The page shows every component of the active theme in every state. These forms carry one field
of each control; the view renders them unbound, disabled and bound with invalid data, each with
its own prefix so that the ids on the page never repeat. The product tiles come in every state:
with and without an image, on sale, new, out of stock and with no price. Later parts of THM.4
add the samples of the other components here.
"""

from typing import Any

from django import forms
from django.templatetags.static import static
from django.utils.translation import gettext_lazy as _

COUNTRIES = [("", _("Choose a country")), ("at", _("Austria")), ("it", _("Italy"))]
DELIVERY = [("standard", _("Standard")), ("express", _("Express"))]
EXTRAS = [("gift", _("Gift wrap")), ("card", _("Greeting card"))]


class TextFieldsForm(forms.Form):
    """Text-like controls: text, email, textarea, select and file."""

    full_name = forms.CharField(
        label=_("Full name"),
        widget=forms.TextInput(attrs={"autocomplete": "name"}),
    )
    email = forms.EmailField(
        label=_("Email"),
        help_text=_("We send the order confirmation here."),
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
    )
    country = forms.ChoiceField(label=_("Country"), choices=COUNTRIES)
    message = forms.CharField(
        label=_("Message to the vendor"), widget=forms.Textarea(attrs={"rows": 3}), required=False
    )
    receipt = forms.FileField(
        label=_("Receipt"), help_text=_("PDF or image, 5 MB at most."), required=False
    )


class ChoicesForm(forms.Form):
    """Choice controls: a single checkbox, a radio group and a checkbox group."""

    delivery = forms.ChoiceField(label=_("Delivery"), choices=DELIVERY, widget=forms.RadioSelect)
    extras = forms.MultipleChoiceField(
        label=_("Extras"),
        choices=EXTRAS,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        help_text=_("Optional, charged at checkout."),
    )
    terms = forms.BooleanField(label=_("I accept the terms of sale"))

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        if data.get("delivery") == "express" and "gift" in data.get("extras", []):
            raise forms.ValidationError(_("Gift wrap is not available with express delivery."))
        return data


# Values that fail validation, so the invalid state shows a field error on every control and
# an error of the whole form.
INVALID_TEXT = {"invalid-text-email": "not-an-email", "invalid-text-message": "Hello"}
INVALID_CHOICES = {"invalid-choices-extras": ["gift"], "invalid-choices-delivery": "express"}


def _disabled(form: forms.Form) -> forms.Form:
    for field in form.fields.values():
        field.disabled = True
    return form


def sample_forms() -> dict[str, forms.Form]:
    """Every form of the page, by state: default, disabled, invalid (bound and validated)."""
    invalid_text = TextFieldsForm(data=INVALID_TEXT, prefix="invalid-text")
    invalid_choices = ChoicesForm(data=INVALID_CHOICES, prefix="invalid-choices")
    invalid_text.is_valid()
    invalid_choices.is_valid()
    return {
        "text": TextFieldsForm(prefix="text"),
        "text_disabled": _disabled(
            TextFieldsForm(prefix="disabled-text", initial={"full_name": "Ada Rossi"})
        ),
        "text_invalid": invalid_text,
        "choices": ChoicesForm(prefix="choices"),
        "choices_disabled": _disabled(
            ChoicesForm(prefix="disabled-choices", initial={"delivery": "standard"})
        ),
        "choices_invalid": invalid_choices,
    }


def sample_products() -> list[dict[str, Any]]:
    """Product tiles in every state. The image is a same-origin static file (CSP img-src)."""
    image = static("styleguide/sample-product.svg")
    return [
        {
            "name": _("Touring jacket"),
            "image_url": image,
            "image_alt": _("Grey placeholder shape"),
            "price": "€ 189.00",
            "vendor": "Vendor A",
            "badges": ["new"],
        },
        {
            "name": _("Rain cover"),
            "image_url": image,
            "image_alt": "",
            "price": "€ 39.00",
            "price_before": "€ 49.00",
            "vendor": "Vendor B",
            "badges": ["sale"],
        },
        {
            "name": _("Winter gloves"),
            "image_url": image,
            "image_alt": "",
            "price": "€ 59.00",
            "vendor": "Vendor C",
            "badges": ["out_of_stock"],
        },
        {
            "name": _("Made-to-measure seat"),
            "image_url": "",
            "price": "",
            "vendor": "Vendor D",
            "badges": ["on_request"],
        },
    ]
