"""TAX.1: staff admin for the tax models. Versioned rows are added through the audited services
and never edited or deleted; every write shows up in the audit log."""

import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import pytest
from django.db.models import QuerySet
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.core.models import AuditLog
from apps.core.tests.factories import utc
from apps.tax import services
from apps.tax.models import TaxRate, Territory, VendorTaxProfile, VendorTaxProfileTranslation
from apps.tax.tests.factories import (
    TaxRateFactory,
    TerritoryFactory,
    VatValidationFactory,
    VendorTaxProfileFactory,
    VendorTaxProfileTranslationFactory,
)
from apps.vendors.engine import FLAT_RATE_REGIME
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

MfaLogin = Callable[[Client, User], Client]


@pytest.fixture
def staff_client(client: Client, superuser: User, mfa_login: MfaLogin) -> Client:
    return mfa_login(client, superuser)


def domain_entries() -> "QuerySet[AuditLog]":
    """Audit entries of this app: signing in staff writes a login entry too (SEC.4)."""
    return AuditLog.objects.filter(action__startswith="tax.")


def rate_form(**overrides: str) -> dict[str, str]:
    data = {
        "country": "ee",
        "territory": "",
        "category": "standard",
        "rate": "22",
        "source": "manual",
        "valid_from_0": "2027-01-01",
        "valid_from_1": "00:00:00",
    }
    data.update(overrides)
    return data


def profile_form(vendor: Any, **overrides: str) -> dict[str, str]:
    data = {
        "vendor": str(vendor.pk),
        "regime": "domestic_only",
        "exempt_reason": "",
        "oss_registered_since": "",
        "valid_from_0": "2027-01-01",
        "valid_from_1": "00:00:00",
    }
    data.update(overrides)
    return data


@pytest.mark.parametrize(
    "model",
    ["taxrate", "territory", "vendortaxprofile", "vendortaxprofiletranslation", "vatvalidation"],
)
def test_changelists_load(staff_client: Client, model: str) -> None:
    TaxRateFactory.create()
    TerritoryFactory.create()
    VendorTaxProfileTranslationFactory.create()
    VatValidationFactory.create()
    response = staff_client.get(reverse(f"admin:tax_{model}_changelist"))
    assert response.status_code == 200


# --- Tax rates


def test_a_rate_added_in_the_admin_goes_through_the_audited_service(staff_client: Client) -> None:
    response = staff_client.post(reverse("admin:tax_taxrate_add"), rate_form())
    assert response.status_code == 302, response.content
    stored = TaxRate.objects.get()
    assert (stored.country, stored.valid_to) == ("EE", None)
    entry = domain_entries().get()
    assert entry.action == "tax.rate.configured"
    assert entry.source == "admin"
    assert entry.actor_label.startswith("staff:")
    assert "@" not in entry.actor_label


def test_a_rate_change_is_visible_in_the_audit_view_with_the_old_and_new_rate(
    staff_client: Client,
) -> None:
    staff_client.post(reverse("admin:tax_taxrate_add"), rate_form())
    staff_client.post(
        reverse("admin:tax_taxrate_add"), rate_form(rate="24", valid_from_0="2028-07-01")
    )
    old, new = TaxRate.objects.order_by("valid_from")
    assert old.valid_to == new.valid_from

    listing = staff_client.get(
        reverse("admin:core_auditlog_changelist"), {"target_model": "tax.taxrate"}
    )
    entries = list(listing.context["cl"].queryset)
    assert {e.target_key for e in entries} == {str(old.public_id), str(new.public_id)}
    newest = AuditLog.objects.get(target_key=str(new.public_id))
    detail = staff_client.get(reverse("admin:core_auditlog_change", args=[newest.pk]))
    assert ("rate", "22.0000", "24.0000") in detail.context["audit_changes"]


def test_the_add_form_reports_a_start_that_is_not_after_the_current_rate(
    staff_client: Client,
) -> None:
    staff_client.post(reverse("admin:tax_taxrate_add"), rate_form())
    response = staff_client.post(reverse("admin:tax_taxrate_add"), rate_form(rate="24"))
    assert response.status_code == 200
    assert "valid_from" in response.context["adminform"].form.errors
    assert TaxRate.objects.count() == 1


def asks_for_the_second_factor(response: Any) -> bool:
    return bool(
        response.status_code == 302 and urlsplit(response["Location"]).path == reverse("mfa:verify")
    )


def test_adding_a_rate_asks_for_the_second_factor_again_after_a_while(
    client: Client, superuser: User
) -> None:
    verified_login(client, superuser, verified_at=time.time() - 3600)
    assert asks_for_the_second_factor(client.get(reverse("admin:tax_taxrate_add")))
    assert asks_for_the_second_factor(client.post(reverse("admin:tax_taxrate_add"), rate_form()))
    assert not TaxRate.objects.exists()
    assert not domain_entries().exists()


def test_rates_cannot_be_changed_or_deleted_from_the_admin(staff_client: Client) -> None:
    rate = TaxRateFactory.create()
    change = reverse("admin:tax_taxrate_change", args=[rate.pk])
    assert staff_client.get(change).status_code == 200  # view-only
    assert staff_client.post(change, rate_form(rate="99")).status_code == 403
    delete = reverse("admin:tax_taxrate_delete", args=[rate.pk])
    assert staff_client.post(delete, {"post": "yes"}).status_code == 403
    assert TaxRate.objects.get(pk=rate.pk).rate == rate.rate


# --- Territories


def test_territories_are_added_and_changed_with_an_audit_entry(staff_client: Client) -> None:
    form = {
        "code": "it-livigno",
        "name": "Livigno",
        "country": "IT",
        "vat_status": "outside_vat_area",
        "postcode_patterns": "23041",
    }
    response = staff_client.post(reverse("admin:tax_territory_add"), form)
    assert response.status_code == 302, response.content
    territory = Territory.objects.get()
    assert territory.code == "IT-LIVIGNO"
    response = staff_client.post(
        reverse("admin:tax_territory_change", args=[territory.pk]), {**form, "name": "Livigno IT"}
    )
    assert response.status_code == 302, response.content
    actions = list(domain_entries().order_by("id").values_list("action", flat=True))
    assert actions == ["tax.territory.created", "tax.territory.updated"]


def test_the_admin_refuses_an_old_style_range_with_the_dotdot_hint(
    staff_client: Client,
) -> None:
    form = {
        "code": "it-co",
        "name": "Como area",
        "country": "IT",
        "vat_status": "own_rates",
        "postcode_patterns": "22100-22199",
    }
    response = staff_client.post(reverse("admin:tax_territory_add"), form)
    assert response.status_code == 200
    errors = response.context["adminform"].form.errors
    assert "22100..22199" in " ".join(errors["postcode_patterns"])
    assert not Territory.objects.exists()


def test_a_territory_with_rates_keeps_its_country_and_code_in_the_admin(
    staff_client: Client,
) -> None:
    territory = TerritoryFactory.create(code="ES-CN", country="ES", name="Canarias")
    TaxRateFactory.create(country="ES", territory=territory)
    change = reverse("admin:tax_territory_change", args=[territory.pk])
    form = {
        "code": "PT-30",
        "name": "Canary Islands",
        "country": "PT",
        "vat_status": territory.vat_status,
        "postcode_patterns": ",".join(territory.postcode_patterns),
    }
    response = staff_client.post(change, form)
    assert response.status_code == 302, response.content
    territory.refresh_from_db()
    assert (territory.country, territory.code, territory.name) == ("ES", "ES-CN", "Canary Islands")


def test_territories_cannot_be_deleted_from_the_admin(staff_client: Client) -> None:
    territory = TerritoryFactory.create()
    delete = reverse("admin:tax_territory_delete", args=[territory.pk])
    assert staff_client.post(delete, {"post": "yes"}).status_code == 403


# --- Vendor tax profiles


def test_a_profile_added_in_the_admin_is_approved_by_the_staff_member(
    staff_client: Client, superuser: User
) -> None:
    vendor = VendorFactory.create()
    response = staff_client.post(
        reverse("admin:tax_vendortaxprofile_add"),
        profile_form(vendor, regime="exempt", exempt_reason="art74_publishing"),
    )
    assert response.status_code == 302, response.content
    profile = VendorTaxProfile.objects.get()
    assert profile.approved_by == superuser
    entry = domain_entries().get()
    assert entry.action == "tax.vendor_tax_profile.configured"
    assert entry.vendor_key == str(vendor.public_id)


def test_a_profile_added_in_the_admin_is_staff_initiated(
    staff_client: Client, superuser: User
) -> None:
    """`requested_by` is not a form field: staff cannot attribute a regime to anyone."""
    vendor = VendorFactory.create()
    someone = UserFactory.create()
    response = staff_client.post(
        reverse("admin:tax_vendortaxprofile_add"),
        {**profile_form(vendor), "requested_by": str(someone.pk)},
    )
    assert response.status_code == 302, response.content
    profile = VendorTaxProfile.objects.get()
    assert (profile.requested_by, profile.approved_by) == (None, superuser)
    add = staff_client.get(reverse("admin:tax_vendortaxprofile_add"))
    assert "requested_by" not in add.context["adminform"].form.fields


def test_adding_a_profile_needs_the_review_permission(client: Client) -> None:
    clerk = UserFactory.create(
        staff=True, permissions=["tax.add_vendortaxprofile", "tax.view_vendortaxprofile"]
    )
    verified_login(client, clerk)
    vendor = VendorFactory.create()
    add = reverse("admin:tax_vendortaxprofile_add")
    assert client.get(add).status_code == 403
    response = client.post(add, profile_form(vendor, regime="exempt", exempt_reason="other"))
    assert response.status_code == 403
    assert not VendorTaxProfile.objects.exists()
    assert not domain_entries().exists()


def test_adding_a_profile_asks_for_the_second_factor_again_after_a_while(
    client: Client, superuser: User
) -> None:
    verified_login(client, superuser, verified_at=time.time() - 3600)
    vendor = VendorFactory.create()
    add = reverse("admin:tax_vendortaxprofile_add")
    assert asks_for_the_second_factor(client.get(add))
    form = profile_form(vendor, regime="exempt", exempt_reason="other")
    assert asks_for_the_second_factor(client.post(add, form))
    assert not VendorTaxProfile.objects.exists()
    assert not domain_entries().exists()


def test_reviewers_add_profiles_with_a_fresh_second_factor(client: Client) -> None:
    reviewer = UserFactory.create(
        staff=True,
        permissions=[
            "tax.add_vendortaxprofile",
            "tax.view_vendortaxprofile",
            services.REVIEW_PERMISSION,
        ],
    )
    verified_login(client, reviewer)
    vendor = VendorFactory.create()
    response = client.post(reverse("admin:tax_vendortaxprofile_add"), profile_form(vendor))
    assert response.status_code == 302, response.content
    assert VendorTaxProfile.objects.get().approved_by == reviewer


def test_the_profile_form_reports_an_inconsistent_fiscal_regime(staff_client: Client) -> None:
    vendor = VendorFactory.create(fiscal_regime=FLAT_RATE_REGIME)
    response = staff_client.post(reverse("admin:tax_vendortaxprofile_add"), profile_form(vendor))
    assert response.status_code == 200
    assert "regime" in response.context["adminform"].form.errors
    assert VendorTaxProfile.objects.count() == 0


def test_profiles_cannot_be_changed_or_deleted_from_the_admin(staff_client: Client) -> None:
    profile = VendorTaxProfileFactory.create()
    change = reverse("admin:tax_vendortaxprofile_change", args=[profile.pk])
    assert staff_client.get(change).status_code == 200
    assert staff_client.post(change, profile_form(profile.vendor, regime="oss")).status_code == 403
    delete = reverse("admin:tax_vendortaxprofile_delete", args=[profile.pk])
    assert staff_client.post(delete, {"post": "yes"}).status_code == 403


def test_legal_notes_are_edited_with_an_audit_entry(staff_client: Client) -> None:
    note = VendorTaxProfileTranslationFactory.create(language="it")
    response = staff_client.post(
        reverse("admin:tax_vendortaxprofiletranslation_change", args=[note.pk]),
        {
            "profile": str(note.profile.pk),
            "language": "it",
            "status": "reviewed",
            "legal_note": "IVA non applicabile: formula di esempio.",
        },
    )
    assert response.status_code == 302, response.content
    stored = VendorTaxProfileTranslation.objects.get(pk=note.pk)
    assert stored.legal_note == "IVA non applicabile: formula di esempio."
    entry = domain_entries().get()
    assert entry.action == "tax.legal_note.updated"
    assert entry.vendor_key == str(note.profile.vendor.public_id)


def test_a_legal_note_cannot_be_moved_to_another_profile(staff_client: Client) -> None:
    note = VendorTaxProfileTranslationFactory.create(language="it")
    original = note.profile
    other = VendorTaxProfileFactory.create(art74=True)
    response = staff_client.post(
        reverse("admin:tax_vendortaxprofiletranslation_change", args=[note.pk]),
        {
            "profile": str(other.pk),
            "language": "it",
            "status": "reviewed",
            "legal_note": "IVA non applicabile: formula di esempio.",
        },
    )
    assert response.status_code == 302, response.content
    assert VendorTaxProfileTranslation.objects.get(pk=note.pk).profile == original


def test_legal_notes_of_an_ended_profile_are_view_only(staff_client: Client) -> None:
    profile = VendorTaxProfileFactory.create(art74=True, valid_from=utc(2025), valid_to=utc(2026))
    note = VendorTaxProfileTranslationFactory.create(profile=profile, language="it")
    change = reverse("admin:tax_vendortaxprofiletranslation_change", args=[note.pk])
    assert staff_client.get(change).status_code == 200
    response = staff_client.post(
        change,
        {"language": "it", "status": "reviewed", "legal_note": "Rewritten history."},
    )
    assert response.status_code == 403
    assert VendorTaxProfileTranslation.objects.get(pk=note.pk).legal_note == note.legal_note
    assert not domain_entries().exists()


# --- VIES results


def test_vies_results_are_view_only(staff_client: Client) -> None:
    check = VatValidationFactory.create()
    assert staff_client.get(reverse("admin:tax_vatvalidation_add")).status_code == 403
    change = reverse("admin:tax_vatvalidation_change", args=[check.pk])
    assert staff_client.get(change).status_code == 200
    assert staff_client.post(change, {"result": "invalid"}).status_code == 403
