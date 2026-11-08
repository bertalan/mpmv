"""VEN.23: the DAC7 seller dataset (owner decision MQ-6) against the real database.

Done when: a TIN without its issuing country is incomplete for a payout; "same as the VAT
number" fills the TIN from a VIES-valid VAT number; a malformed TIN for its country is refused;
a vendor whose country issues no TIN is complete with a place of birth; a staff correction
without step-up is refused and a saved one is in the AuditLog. Also: the DAC7 check is the one
source of the payout gate, so deactivating requirement rows cannot let an incomplete vendor
through, and the new personal data are encrypted at rest and masked in the audit log.
"""

import datetime as dt
import time
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.vendors import engine
from apps.vendors.models import LegalFieldRequirement, Vendor
from apps.vendors.services import (
    DAC7_CORRECTED,
    assert_payout_allowed,
    check_vat_number,
    correct_dac7_dataset,
    legal_profile_completeness,
    save_vendor,
)
from apps.vendors.tests.factories import VendorFactory, iban, italian_vat
from apps.vendors.tests.fakes import FakeVatNumberValidator, seed_legal_requirements

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:reviewer", source="admin", ip="127.0.0.1")
TRAITS = (
    "italian_company",
    "italian_sole_proprietor",
    "eu_company",
    "non_eu_company",
    "non_eu_without_vat",
)


@pytest.fixture
def seeded() -> None:
    seed_legal_requirements()


def raw_column(vendor: Vendor, column: str) -> str:
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {connection.ops.quote_name(column)} FROM vendors_vendor WHERE id = %s",  # noqa: S608
            [vendor.pk],
        )
        return str(cursor.fetchone()[0])


# --- The payout gate


@pytest.mark.parametrize("trait", TRAITS)
def test_complete_traits_have_the_dac7_dataset(seeded: None, trait: str) -> None:
    vendor = VendorFactory.create(**{trait: True})
    assert engine.dac7_missing(vendor.legal_identity()) == ()
    assert_payout_allowed(vendor)  # does not raise


@pytest.mark.parametrize("trait", TRAITS)
def test_a_tin_without_its_issuing_country_blocks_the_payout(seeded: None, trait: str) -> None:
    vendor = VendorFactory.create(**{trait: True})
    Vendor.objects.filter(pk=vendor.pk).update(tin_country="")
    with pytest.raises(engine.PayoutNotAllowed) as excinfo:
        assert_payout_allowed(vendor)
    assert "tin_country" in excinfo.value.missing


def test_deactivating_every_requirement_row_cannot_relax_dac7(seeded: None) -> None:
    """Audit finding: the gate read only the rows, so DAC7 could be switched off by staff."""
    vendor = VendorFactory.create(eu_company=True)
    LegalFieldRequirement.objects.update(active=False)
    Vendor.objects.filter(pk=vendor.pk).update(tax_code="", iban="")
    with pytest.raises(engine.PayoutNotAllowed) as excinfo:
        assert_payout_allowed(vendor)
    assert excinfo.value.missing == ("tax_code", "iban")
    stored = Vendor.objects.get(pk=vendor.pk)
    assert legal_profile_completeness(stored).missing == ("tax_code", "iban")


def test_an_individual_without_a_date_of_birth_cannot_be_paid(seeded: None) -> None:
    vendor = VendorFactory.create(italian_sole_proprietor=True)
    Vendor.objects.filter(pk=vendor.pk).update(date_of_birth=None)
    with pytest.raises(engine.PayoutNotAllowed) as excinfo:
        assert_payout_allowed(vendor)
    assert excinfo.value.missing == ("date_of_birth",)


def test_an_entity_without_a_registration_number_cannot_be_paid(seeded: None) -> None:
    vendor = VendorFactory.create(italian_company=True)
    Vendor.objects.filter(pk=vendor.pk).update(business_registration_number="")
    with pytest.raises(engine.PayoutNotAllowed) as excinfo:
        assert_payout_allowed(vendor)
    assert excinfo.value.missing == ("business_registration_number",)


def test_the_seeded_rows_list_the_dac7_dataset(seeded: None) -> None:
    active = set(
        LegalFieldRequirement.objects.filter(active=True).values_list(
            "country", "region", "legal_form", "field_name"
        )
    )
    assert ("", "", "", "tin_country") in active
    assert ("", "", "sole_proprietorship", "date_of_birth") in active
    for form in ("partnership", "srl", "spa", "cooperative", "other"):
        assert ("", "", form, "business_registration_number") in active
    # The TIN of a non-EU vendor is decided by the DAC7 check (a place of birth may replace it).
    assert ("", "non_eu", "", "tax_code") not in active


def test_a_vendor_whose_country_issues_no_tin_is_paid_with_a_place_of_birth(
    seeded: None,
) -> None:
    vendor = VendorFactory.create(
        country="BS",
        legal_form=Vendor.LegalForm.SOLE_PROPRIETORSHIP,
        first_name="Ann",
        last_name="Example",
        legal_name="Ann Example",
        vat_number="",
        tax_code="",
        tin_country="BS",
        tax_residence_country="BS",
        date_of_birth=dt.date(1980, 5, 1),
        registered_street="Example Street",
        registered_postal_code="N-1234",
        registered_city="Nassau",
        registered_country="BS",
        iban=iban("GB", "EXMP00000000000001"),
    )
    with pytest.raises(engine.PayoutNotAllowed) as excinfo:
        assert_payout_allowed(vendor)
    assert excinfo.value.missing == ("place_of_birth",)
    Vendor.objects.filter(pk=vendor.pk).update(place_of_birth="Nassau, Bahamas")
    assert_payout_allowed(vendor)  # does not raise


# --- Saving: formats, "same as the VAT number", encryption


def test_a_malformed_tin_for_its_country_is_refused() -> None:
    vendor = VendorFactory.create(eu_company=True)
    vendor.tax_code = "1234"
    with pytest.raises(ValidationError) as excinfo:
        save_vendor(vendor, ctx=CTX)
    assert "tax_code" in excinfo.value.message_dict
    assert Vendor.objects.get(pk=vendor.pk).tax_code != "1234"


def test_same_as_vat_fills_the_tin_when_vies_says_valid() -> None:
    vendor = VendorFactory.create(
        italian_company=True, tax_code="", tin_country="", tin_same_as_vat=True
    )
    check_vat_number(vendor, FakeVatNumberValidator(default=engine.VIES_VALID), ctx=CTX)
    stored = Vendor.objects.get(pk=vendor.pk)
    assert (stored.tax_code, stored.tin_country) == (vendor.vat_number, "IT")
    entry = AuditLog.objects.get(action="vendor.vat_checked")
    assert entry.after is not None
    assert vendor.vat_number not in str(entry.after.get("tax_code"))


def test_same_as_vat_leaves_the_tin_empty_when_vies_says_invalid() -> None:
    vendor = VendorFactory.create(
        italian_company=True, tax_code="", tin_country="", tin_same_as_vat=True
    )
    check_vat_number(vendor, FakeVatNumberValidator(default=engine.VIES_INVALID), ctx=CTX)
    assert Vendor.objects.get(pk=vendor.pk).tax_code == ""


def test_same_as_vat_follows_a_new_vat_number_only_after_vies() -> None:
    vendor = VendorFactory.create(italian_company=True, tin_same_as_vat=True)
    check_vat_number(vendor, FakeVatNumberValidator(default=engine.VIES_VALID), ctx=CTX)
    vendor.refresh_from_db()
    vendor.vat_number = italian_vat(987)
    save_vendor(vendor, ctx=CTX)
    stored = Vendor.objects.get(pk=vendor.pk)
    assert stored.vies_status == engine.VIES_UNCHECKED
    assert stored.tax_code == ""  # the old VAT number is no longer the TIN


def test_date_and_place_of_birth_are_encrypted_at_rest_and_masked_in_the_audit() -> None:
    vendor = VendorFactory.create(italian_sole_proprietor=True)
    vendor.place_of_birth = "Bergamo"
    vendor.date_of_birth = dt.date(1979, 3, 4)
    save_vendor(vendor, ctx=CTX)
    assert raw_column(vendor, "date_of_birth").startswith("enc1:")
    assert raw_column(vendor, "place_of_birth").startswith("enc1:")
    stored = Vendor.objects.get(pk=vendor.pk)
    assert stored.date_of_birth == dt.date(1979, 3, 4)
    assert stored.place_of_birth == "Bergamo"
    entry = AuditLog.objects.filter(action="vendor.updated").latest("id")
    text = str(entry.before) + str(entry.after)
    assert "1979" not in text
    assert "Bergamo" not in text


# --- Staff corrections: MFA step-up and audit


def test_a_staff_correction_without_step_up_is_refused() -> None:
    vendor = VendorFactory.create(eu_company=True)
    vendor.tax_code = "98765432109"
    with pytest.raises(engine.StepUpRequired):
        correct_dac7_dataset(vendor, ctx=CTX, stepped_up=False)
    assert Vendor.objects.get(pk=vendor.pk).tax_code != "98765432109"
    assert not AuditLog.objects.filter(action=DAC7_CORRECTED).exists()


def test_a_staff_correction_with_step_up_is_saved_and_audited_masked() -> None:
    vendor = VendorFactory.create(eu_company=True)
    old_tin = vendor.tax_code
    vendor.tax_code = "98765432109"
    preview = correct_dac7_dataset(vendor, ctx=CTX, stepped_up=True, dry_run=True)
    assert preview.changes
    assert not AuditLog.objects.filter(action=DAC7_CORRECTED).exists()

    correct_dac7_dataset(vendor, ctx=CTX, stepped_up=True)
    assert Vendor.objects.get(pk=vendor.pk).tax_code == "98765432109"
    entry = AuditLog.objects.get(action=DAC7_CORRECTED)
    assert entry.before is not None
    assert entry.after is not None
    assert entry.before["tax_code"] == engine.mask_tax_code(old_tin)
    assert entry.after["tax_code"] == engine.mask_tax_code("98765432109")
    assert "98765432109" not in str(entry.after)


# --- The admin


EMPTY_INLINE = {
    "translations-TOTAL_FORMS": "0",
    "translations-INITIAL_FORMS": "0",
    "translations-MIN_NUM_FORMS": "0",
    "translations-MAX_NUM_FORMS": "1000",
}


def change_form(vendor: Vendor, **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        name: getattr(vendor, name)
        for name in (
            "display_name",
            "slug",
            "legal_name",
            "country",
            "contact_email",
            "phone",
            "sales_mode_default",
            *engine.LEGAL_FIELDS,
        )
    }
    data = {name: "" if value is None else value for name, value in data.items()}
    if vendor.tin_same_as_vat:
        data["tin_same_as_vat"] = "on"
    data["legacy_wp_user_id"] = ""
    data.update(EMPTY_INLINE)
    data.update(overrides)
    return data


@pytest.fixture
def staff() -> User:
    return UserFactory.create(email="ops@example.com", superuser=True)


def test_the_admin_refuses_a_dac7_correction_without_a_fresh_second_factor(
    client: Client, staff: User
) -> None:
    verified_login(client, staff, verified_at=time.time() - 3600)
    vendor = VendorFactory.create(eu_company=True)
    url = reverse("admin:vendors_vendor_change", args=[vendor.pk])
    response = client.post(url, change_form(vendor, tax_code="98765432109"))
    assert response.status_code == 200
    assert "fresh second-factor verification" in response.content.decode()
    assert Vendor.objects.get(pk=vendor.pk).tax_code == vendor.tax_code
    assert not AuditLog.objects.filter(action=DAC7_CORRECTED).exists()


def test_the_admin_saves_other_fields_without_step_up(client: Client, staff: User) -> None:
    verified_login(client, staff, verified_at=time.time() - 3600)
    vendor = VendorFactory.create(eu_company=True)
    url = reverse("admin:vendors_vendor_change", args=[vendor.pk])
    response = client.post(url, change_form(vendor, phone="+49 30 1234567"))
    assert response.status_code == 302, response.content.decode()
    assert Vendor.objects.get(pk=vendor.pk).phone == "+49 30 1234567"


def test_the_admin_saves_a_dac7_correction_after_step_up(client: Client, staff: User) -> None:
    verified_login(client, staff)
    vendor = VendorFactory.create(eu_company=True)
    url = reverse("admin:vendors_vendor_change", args=[vendor.pk])
    response = client.post(
        url, change_form(vendor, tax_code="98765432109", date_of_birth="1980-01-02")
    )
    assert response.status_code == 302, response.content.decode()
    assert Vendor.objects.get(pk=vendor.pk).tax_code == "98765432109"
    entry = AuditLog.objects.get(action=DAC7_CORRECTED)
    assert entry.source == "admin"
    assert "98765432109" not in str(entry.after)
    assert "1980" not in str(entry.after)
