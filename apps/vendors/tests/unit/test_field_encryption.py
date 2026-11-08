"""SEC.3: which vendor fields are encrypted at rest, and how the audit log masks them.

Encrypted: the IBAN (VEN.12), the tax code / TIN (DAC7; for sole proprietors it is a personal
identifier) and the date and place of birth of sole proprietors (DAC7, VEN.23). Not encrypted:
the VAT number, which is public in VIES and searched by staff.
"""

import pytest
from django.core.exceptions import FieldError

from apps.core.fields import EncryptedFieldMixin
from apps.vendors import engine
from apps.vendors.models import Vendor
from apps.vendors.services import MASKED_FIELDS, vendor_snapshot
from apps.vendors.tests.factories import VendorFactory


def encrypted_field_names() -> set[str]:
    return {f.name for f in Vendor._meta.get_fields() if isinstance(f, EncryptedFieldMixin)}


def test_the_iban_the_tax_code_and_the_birth_data_are_encrypted_at_rest() -> None:
    assert encrypted_field_names() == {"iban", "tax_code", "date_of_birth", "place_of_birth"}


def test_every_encrypted_field_is_masked_in_the_audit_log() -> None:
    assert set(MASKED_FIELDS) == encrypted_field_names()


def test_the_snapshot_never_holds_an_encrypted_value_in_clear() -> None:
    vendor = VendorFactory.build(italian_sole_proprietor=True)
    snapshot = vendor_snapshot(vendor)
    assert snapshot["iban"] == engine.mask_iban(vendor.iban)
    assert snapshot["tax_code"] == engine.mask_tax_code(vendor.tax_code)
    assert vendor.iban not in str(snapshot)
    assert vendor.tax_code not in str(snapshot)


def test_mask_tax_code_keeps_the_last_four_characters_of_a_long_code() -> None:
    assert engine.mask_tax_code("RSSMRA85T10A562S") == "*" * 12 + "562S"
    assert engine.mask_tax_code("123456789012") == "********9012"
    assert engine.mask_tax_code("") == ""


@pytest.mark.parametrize(
    ("tax_code", "masked"),
    [
        ("12-3456789", "*******789"),  # a 9-digit TIN: four would show almost half of it
        ("123456", "****56"),
        ("1234", "***4"),
        ("12", "**"),
    ],
)
def test_mask_tax_code_shows_at_most_a_third_of_a_short_code(tax_code: str, masked: str) -> None:
    assert engine.mask_tax_code(tax_code) == masked
    shown = len(masked.lstrip("*"))
    assert shown * 3 <= len(tax_code)


def test_encrypted_vendor_fields_cannot_be_searched() -> None:
    with pytest.raises(FieldError):
        Vendor.objects.filter(tax_code="RSSMRA85T10A562S")
    with pytest.raises(FieldError):
        Vendor.objects.filter(iban__startswith="IT")
