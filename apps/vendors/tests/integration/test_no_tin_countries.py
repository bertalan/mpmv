"""VEN.27: no-TIN jurisdictions as staff-editable data (owner decision MQ-16).

Done when: a data migration seeds the six current countries and the DAC7 check gives the same
results as before; a country staff add makes an individual with a place of birth and no TIN
complete, and removing it makes the vendor incomplete again; each change is in the AuditLog with
its reason, and a dry run changes nothing; vendor members cannot edit the list; the engine has
no ORM import.
"""

import datetime as dt
from pathlib import Path
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from apps.accounts.tests.factories import UserFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.vendors import engine
from apps.vendors.models import NoTinCountry, Vendor
from apps.vendors.services import (
    NO_TIN_ADDED,
    NO_TIN_REMOVED,
    add_no_tin_country,
    legal_profile_completeness,
    no_tin_countries,
    onboarding_checklist,
    remove_no_tin_country,
)
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory, iban
from apps.vendors.tests.fakes import seed_legal_requirements

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:reviewer", source="admin", ip="127.0.0.1")
REASON = "Confirmed by the accountant."


def no_tin_vendor(country: str, **overrides: Any) -> Vendor:
    return VendorFactory.create(
        country=country,
        legal_form=Vendor.LegalForm.SOLE_PROPRIETORSHIP,
        first_name="Ann",
        last_name="Example",
        legal_name="Ann Example",
        vat_number="",
        tax_code="",
        tin_country=country,
        tax_residence_country=country,
        date_of_birth=dt.date(1980, 5, 1),
        place_of_birth="Wellington",
        registered_street="Example Street",
        registered_postal_code="6011",
        registered_city="Wellington",
        registered_country=country,
        iban=iban("GB", "EXMP00000000000001"),
        **overrides,
    )


def missing(vendor: Vendor) -> tuple[str, ...]:
    return legal_profile_completeness(vendor).missing


def test_a_data_migration_seeds_the_six_current_countries() -> None:
    assert set(NoTinCountry.objects.values_list("country", flat=True)) == {
        "AE",
        "BM",
        "BS",
        "KY",
        "MC",
        "VG",
    }
    assert no_tin_countries() == engine.SEEDED_NO_TIN_COUNTRIES


def test_a_seeded_country_behaves_as_it_did_with_the_constant() -> None:
    seed_legal_requirements()
    vendor = no_tin_vendor("BS")
    assert "tax_code" not in missing(vendor)
    Vendor.objects.filter(pk=vendor.pk).update(place_of_birth="")
    vendor.refresh_from_db()
    assert "place_of_birth" in missing(vendor)


def test_adding_a_country_makes_an_individual_with_a_place_of_birth_complete() -> None:
    vendor = no_tin_vendor("NZ")
    assert "tax_code" in missing(vendor)
    add_no_tin_country("NZ", reason=REASON, ctx=CTX)
    assert "tax_code" not in missing(vendor)
    assert "NZ" in no_tin_countries()


def test_removing_a_country_makes_the_vendor_incomplete_again() -> None:
    vendor = no_tin_vendor("BS")
    assert "tax_code" not in missing(vendor)
    remove_no_tin_country("BS", reason=REASON, ctx=CTX)
    assert "tax_code" in missing(vendor)
    assert "BS" not in no_tin_countries()


def test_the_onboarding_checklist_follows_the_table_too() -> None:
    vendor = no_tin_vendor("NZ")
    assert not {i.key: i for i in onboarding_checklist(vendor).items}[engine.DAC7].done
    add_no_tin_country("NZ", reason=REASON, ctx=CTX)
    assert {i.key: i for i in onboarding_checklist(vendor).items}[engine.DAC7].done


def test_each_change_is_in_the_audit_log_with_its_reason() -> None:
    add_no_tin_country("NZ", reason=REASON, ctx=CTX)
    remove_no_tin_country("NZ", reason="Added by mistake.", ctx=CTX)
    added = AuditLog.objects.get(action=NO_TIN_ADDED, target_key="NZ")
    removed = AuditLog.objects.get(action=NO_TIN_REMOVED, target_key="NZ")
    assert added.after["reason"] == REASON
    assert added.actor_label == "staff:reviewer"
    assert removed.before["removal_reason"] == "Added by mistake."


def test_a_dry_run_returns_the_change_and_changes_nothing() -> None:
    changes = add_no_tin_country("NZ", reason=REASON, ctx=CTX, dry_run=True)
    assert changes.summary() == {"create": 1, "update": 0, "delete": 0}
    assert not NoTinCountry.objects.filter(country="NZ").exists()
    changes = remove_no_tin_country("BS", reason=REASON, ctx=CTX, dry_run=True)
    assert changes.summary() == {"create": 0, "update": 0, "delete": 1}
    assert NoTinCountry.objects.filter(country="BS").exists()
    assert not AuditLog.objects.filter(action__in=[NO_TIN_ADDED, NO_TIN_REMOVED]).exists()


def test_a_reason_is_required() -> None:
    with pytest.raises(ValidationError):
        add_no_tin_country("NZ", reason="  ", ctx=CTX)
    with pytest.raises(ValidationError):
        remove_no_tin_country("BS", reason="", ctx=CTX)
    assert NoTinCountry.objects.filter(country="BS").exists()


def test_the_country_is_validated_and_normalised() -> None:
    with pytest.raises(ValidationError):
        add_no_tin_country("ZZZ", reason=REASON, ctx=CTX)
    with pytest.raises(ValidationError):
        add_no_tin_country("BS", reason=REASON, ctx=CTX)  # already listed
    add_no_tin_country("nz", reason=REASON, ctx=CTX)
    assert "NZ" in no_tin_countries()


def test_removing_an_unlisted_country_is_refused() -> None:
    with pytest.raises(ValidationError):
        remove_no_tin_country("NZ", reason=REASON, ctx=CTX)


def test_vendor_members_cannot_edit_the_list(client: Client) -> None:
    member = VendorMemberFactory.create().user
    client.force_login(member)
    for name in ("admin:vendors_notincountry_changelist", "admin:vendors_notincountry_add"):
        response = client.get(reverse(name))
        assert response.status_code in (302, 403)


def test_staff_without_the_permission_cannot_edit_the_list(client: Client) -> None:
    staff = UserFactory.create(staff=True)
    client.force_login(staff)
    assert client.get(reverse("admin:vendors_notincountry_add")).status_code in (302, 403)


def test_the_engine_has_no_orm_import() -> None:
    source = (Path(engine.__file__)).read_text()
    assert "django" not in source
