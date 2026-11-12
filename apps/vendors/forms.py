"""The public "Sell with us" application form (VEN.22).

Only what the application needs (data minimisation): the applicant's name and email, the name
of the business and its country. Everything else is collected later, during onboarding (VEN.5).
The form takes no password: the person who opens the emailed link chooses it on the confirm
page. Every field has a length cap, so a request cannot make us store or hash a huge value. The
widgets carry the `autocomplete` tokens password managers and browsers fill from (CMP.9, WCAG
2.2 SC 3.3.8) and no paste handler; the password is asked once (SC 3.3.7, redundant entry).
"""

from typing import Any

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.services import allowed_vendor_countries

# English names of the EU-27, the go-live allowed countries (CMP.13, D-21). A country staff add
# later shows its ISO code until it joins this table: there is no country-name library yet.
COUNTRY_NAMES = {
    "AT": "Austria", "BE": "Belgium", "BG": "Bulgaria", "HR": "Croatia", "CY": "Cyprus",
    "CZ": "Czechia", "DK": "Denmark", "EE": "Estonia", "FI": "Finland", "FR": "France",
    "DE": "Germany", "GR": "Greece", "HU": "Hungary", "IE": "Ireland",
    "IT": "Italy",  # country-literal: display name in a table of EU names
    "LV": "Latvia", "LT": "Lithuania", "LU": "Luxembourg", "MT": "Malta", "NL": "Netherlands",
    "PL": "Poland", "PT": "Portugal", "RO": "Romania", "SK": "Slovakia", "SI": "Slovenia",
    "ES": "Spain", "SE": "Sweden",
}  # fmt: skip

NAME_MAX = 150
EMAIL_MAX = 254
PASSWORD_MAX = 128
MAX_BODY_BYTES = 8 * 1024  # the four fields fit in well under 2 KB, plus the tokens


class ApplicationForm(forms.Form):
    full_name = forms.CharField(
        label="Your full name",
        help_text=_(
            "Your first and last name. We use it to greet you and to set up your account. "
            "Required: we cannot process the application without it."
        ),
        max_length=NAME_MAX,
        widget=forms.TextInput(attrs={"autocomplete": "name"}),
    )
    email = forms.EmailField(
        label="Email address",
        help_text=_(
            "The address where we send the link to confirm your application. Required: "
            "without a working address you cannot continue."
        ),
        max_length=EMAIL_MAX,
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
    )
    business_name = forms.CharField(
        label="Name of your business or shop",
        help_text=_(
            "The name of your business or shop, for example 'Rossi Ceramics'. Required: the "
            "application needs it."
        ),
        max_length=NAME_MAX,
        widget=forms.TextInput(attrs={"autocomplete": "organization"}),
    )
    # The country of establishment: required by the vendor record, and the only countries
    # offered are those the platform accepts (CMP.13), so nobody applies in vain.
    country = forms.ChoiceField(
        label="Country where your business is established",
        help_text=_(
            "The country where your business is established. Only the countries we accept at "
            "the moment are listed. Required: it decides which details we ask you for later."
        ),
        widget=forms.Select(attrs={"autocomplete": "country"}),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        names = sorted(
            ((code, COUNTRY_NAMES.get(code, code)) for code in allowed_vendor_countries()),
            key=lambda item: item[1],
        )
        choices = [("", "---------"), *names]
        self.fields["country"].choices = choices  # type: ignore[attr-defined]


class ConfirmForm(forms.Form):
    """The confirm page: whoever opens the emailed link chooses the password (VEN.22)."""

    # Not stripped: a password may start or end with a space.
    password = forms.CharField(
        label="Choose a password",
        help_text=_(
            "Pick a password you do not use anywhere else. You will use it with your email "
            "address to log in. Required: without it your account is not created."
        ),
        max_length=PASSWORD_MAX,
        strip=False,
        widget=forms.PasswordInput(
            attrs={"autocomplete": "new-password", "data-password-toggle": "Show password"}
        ),
    )
