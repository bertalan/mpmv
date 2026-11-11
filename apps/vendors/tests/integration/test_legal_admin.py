"""VEN.12: staff edit the vendor legal identity and the legal field requirements in the admin.

An incomplete profile can always be opened and saved; the change form shows what a payout
would still need. Requirement rows are staff-editable data, and every change is audited.
"""

from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.core.models import AuditLog
from apps.vendors import engine
from apps.vendors.models import LegalFieldRequirement, Vendor
from apps.vendors.tests.factories import LegalFieldRequirementFactory, VendorFactory
from apps.vendors.tests.fakes import seed_legal_requirements

pytestmark = pytest.mark.django_db

EMPTY_INLINE = {
    "translations-TOTAL_FORMS": "0",
    "translations-INITIAL_FORMS": "0",
    "translations-MIN_NUM_FORMS": "0",
    "translations-MAX_NUM_FORMS": "1000",
}


@pytest.fixture
def staff_client(client: Client, superuser: User, mfa_login: Any) -> Client:
    seed_legal_requirements()
    logged_in: Client = mfa_login(client, superuser)
    return logged_in


def change_form(vendor: Vendor, **overrides: Any) -> dict[str, Any]:
    """The POST data of the vendor change form, as the browser would send it unchanged."""
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
    data["share_capital"] = "" if vendor.share_capital is None else str(vendor.share_capital)
    data["legacy_wp_user_id"] = ""
    data.update(EMPTY_INLINE)
    data.update(overrides)
    return data


def test_change_form_groups_the_legal_fields_and_offers_dropdowns(staff_client: Client) -> None:
    vendor = VendorFactory.create(italian_company=True)
    response = staff_client.get(reverse("admin:vendors_vendor_change", args=[vendor.pk]))
    assert response.status_code == 200
    content = response.content.decode()
    for title in (
        "Registered office",
        "Tax identity",
        "Company register (REA)",
        "E-invoicing",
        "Invoicing bank details",
        "Permanent establishment",
        "Fiscal representative",
    ):
        assert title in content
    assert '<select name="fiscal_regime"' in content
    assert "RF19 - Regime forfettario (art.1, c.54-89, L. 190/2014)" in content
    assert '<select name="legal_form"' in content
    assert '<select name="rea_office"' in content


def test_change_form_shows_what_a_payout_still_needs(staff_client: Client) -> None:
    vendor = VendorFactory.create(italian_company=True, iban="", pec="")
    response = staff_client.get(reverse("admin:vendors_vendor_change", args=[vendor.pk]))
    assert "Missing for payout" in response.content.decode()
    assert "Missing: PEC address, IBAN." in response.content.decode()

    complete = VendorFactory.create(italian_company=True)
    response = staff_client.get(reverse("admin:vendors_vendor_change", args=[complete.pk]))
    assert "the legal profile is complete for payouts" in response.content.decode()


def test_an_incomplete_profile_can_be_opened_and_saved(staff_client: Client) -> None:
    vendor = VendorFactory.create(vat_number="", legal_form=Vendor.LegalForm.SRL)
    url = reverse("admin:vendors_vendor_change", args=[vendor.pk])
    assert staff_client.get(url).status_code == 200

    response = staff_client.post(url, change_form(vendor, registered_city="Rome"))
    assert response.status_code == 302, response.content.decode()
    vendor.refresh_from_db()
    assert vendor.registered_city == "Rome"
    assert vendor.iban == ""


def test_an_invalid_iban_is_refused_and_nothing_is_saved(staff_client: Client) -> None:
    vendor = VendorFactory.create(italian_company=True)
    url = reverse("admin:vendors_vendor_change", args=[vendor.pk])
    response = staff_client.post(url, change_form(vendor, iban="IT60X0542811101000000123457"))
    assert response.status_code == 200
    assert "Not a valid IBAN (check digits or length)." in response.content.decode()
    assert Vendor.objects.get(pk=vendor.pk).iban == vendor.iban


def test_saving_an_iban_writes_a_masked_audit_row(staff_client: Client) -> None:
    vendor = VendorFactory.create(italian_company=True, iban="")
    url = reverse("admin:vendors_vendor_change", args=[vendor.pk])
    new_iban = "IT60 X054 2811 1010 0000 0123 456"
    response = staff_client.post(url, change_form(vendor, iban=new_iban))
    assert response.status_code == 302, response.content.decode()

    vendor.refresh_from_db()
    assert vendor.iban == "IT60X0542811101000000123456"
    entry = AuditLog.objects.get(action="vendor.dac7_corrected")  # the IBAN is DAC7 data
    assert entry.source == "admin"
    assert entry.actor_label.startswith("staff:")
    assert "@" not in entry.actor_label
    assert entry.after is not None
    assert entry.after["iban"] == engine.mask_iban(vendor.iban)
    assert vendor.iban not in str(entry.after)


def test_staff_add_a_requirement_and_it_is_audited(staff_client: Client) -> None:
    response = staff_client.post(
        reverse("admin:vendors_legalfieldrequirement_add"),
        {
            "country": "FR",
            "legal_form": "",
            "field_name": "eori",
            "purpose": "payout",
            "active": "on",
            "note": "Customs.",
        },
    )
    assert response.status_code == 302, response.content.decode()
    rule = LegalFieldRequirement.objects.get(country="FR", field_name="eori")
    entry = AuditLog.objects.get(action="vendor.legal_requirement_saved")
    assert entry.target_key == str(rule.public_id)
    assert entry.source == "admin"


def test_staff_deactivate_and_delete_requirements_with_audit(staff_client: Client) -> None:
    rule = LegalFieldRequirementFactory.create(country="FR", field_name="eori")
    url = reverse("admin:vendors_legalfieldrequirement_change", args=[rule.pk])
    response = staff_client.post(
        url,
        {"country": "FR", "legal_form": "", "field_name": "eori", "purpose": "payout", "note": ""},
    )
    assert response.status_code == 302, response.content.decode()
    rule.refresh_from_db()
    assert rule.active is False
    saved = AuditLog.objects.get(action="vendor.legal_requirement_saved")
    assert saved.after is not None
    assert saved.after["active"] is False

    response = staff_client.post(
        reverse("admin:vendors_legalfieldrequirement_changelist"),
        {"action": "delete_selected", "_selected_action": [rule.pk], "post": "yes"},
    )
    assert response.status_code == 302
    assert not LegalFieldRequirement.objects.filter(pk=rule.pk).exists()
    assert AuditLog.objects.filter(action="vendor.legal_requirement_deleted").count() == 1


def test_requirement_field_must_be_a_legal_field(staff_client: Client) -> None:
    response = staff_client.post(
        reverse("admin:vendors_legalfieldrequirement_add"),
        {"country": "", "legal_form": "", "field_name": "contact_email", "purpose": "payout"},
    )
    assert response.status_code == 200
    assert "Select a valid choice" in response.content.decode()


def test_requirement_changelist_lists_the_seeded_rules(staff_client: Client) -> None:
    response = staff_client.get(
        reverse("admin:vendors_legalfieldrequirement_changelist"), {"country": "IT"}
    )
    assert response.status_code == 200
    fields = {row.field_name for row in response.context["cl"].result_list}
    assert {"pec", "tax_code", "fiscal_regime", "sdi_recipient_code", "iban"} <= fields
