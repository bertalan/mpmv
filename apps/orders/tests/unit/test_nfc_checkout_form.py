"""I18N.10: the checkout details form stores names and the address in NFC."""

import secrets
import unicodedata

from apps.orders.ui.forms import CheckoutDetailsForm


def decomposed() -> str:
    """A generated text with an "e" and a combining acute accent."""
    return f"x{secrets.token_hex(4)}e\u0301"


def test_the_checkout_text_is_stored_in_nfc_and_the_email_stays_exact() -> None:
    texts = {name: decomposed() for name in ("full_name", "line1", "line2", "city", "state")}
    email = f"{secrets.token_hex(4)}@xe\u0301.example"
    form = CheckoutDetailsForm({**texts, "email": email, "postcode": "00100", "country": "IT"})
    assert form.is_valid(), form.errors
    for name, value in texts.items():
        assert form.cleaned_data[name] == unicodedata.normalize("NFC", value)
    assert form.address()["name"] == unicodedata.normalize("NFC", texts["full_name"])
    assert CheckoutDetailsForm.nfc_exact_fields == ("email", "vat_id")
