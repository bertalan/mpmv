"""VEN.27: no-TIN jurisdictions as staff-editable data (owner decision MQ-16).

Done when: a data migration seeds the six current countries and the DAC7 check gives the same
results as before; a country staff add makes an individual with a place of birth and no TIN
complete, and removing it makes the vendor incomplete again; each change is in the AuditLog with
its reason, and a dry run changes nothing; vendor members cannot edit the list; the engine has
no ORM import.
"""

import ast
import datetime as dt
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
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
from apps.vendors.tests.factories import (
    NoTinCountryFactory,
    VendorFactory,
    VendorMemberFactory,
    iban,
)
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
    # Only the TIN is relaxed: the other payout requirements still apply.
    Vendor.objects.filter(pk=vendor.pk).update(iban="", registered_street="")
    vendor.refresh_from_db()
    assert {"iban", "registered_street"} <= set(missing(vendor))
    assert "tax_code" not in missing(vendor)


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
    assert (added.after or {})["reason"] == REASON
    assert added.actor_label == "staff:reviewer"
    assert (removed.before or {})["removal_reason"] == "Added by mistake."


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


URLS = {
    "changelist": lambda row: reverse("admin:vendors_notincountry_changelist"),
    "add": lambda row: reverse("admin:vendors_notincountry_add"),
    "delete": lambda row: reverse("admin:vendors_notincountry_delete", args=[row.pk]),
}


@pytest.mark.parametrize("url", URLS)
def test_anonymous_users_are_sent_to_the_login(client: Client, url: str) -> None:
    row = NoTinCountry.objects.get(country="BS")
    response = client.get(URLS[url](row))
    assert response.status_code == 302
    assert "login" in response["Location"]


@pytest.mark.parametrize("url", URLS)
def test_vendor_members_cannot_edit_the_list(client: Client, url: str) -> None:
    client.force_login(VendorMemberFactory.create().user)
    row = NoTinCountry.objects.get(country="BS")
    assert client.get(URLS[url](row)).status_code in (302, 403)
    assert client.post(URLS[url](row), {"post": "yes", "reason": REASON}).status_code in (302, 403)
    assert NoTinCountry.objects.filter(country="BS").exists()


@pytest.mark.parametrize("url", URLS)
def test_staff_without_the_permission_get_403(client: Client, url: str, mfa_login: Any) -> None:
    client = mfa_login(client, UserFactory.create(staff=True))
    row = NoTinCountry.objects.get(country="BS")
    assert client.get(URLS[url](row)).status_code == 403
    assert client.post(URLS[url](row), {"post": "yes", "reason": REASON}).status_code == 403
    assert NoTinCountry.objects.filter(country="BS").exists()


def test_staff_with_only_the_add_permission_cannot_delete(client: Client, mfa_login: Any) -> None:
    staff = UserFactory.create(staff=True)
    staff.user_permissions.add(Permission.objects.get(codename="add_notincountry"))
    client = mfa_login(client, staff)
    row = NoTinCountry.objects.get(country="BS")
    assert (
        client.get(reverse("admin:vendors_notincountry_delete", args=[row.pk])).status_code == 403
    )
    assert client.get(reverse("admin:vendors_notincountry_changelist")).status_code == 403


def test_a_removal_without_the_permission_is_403_even_with_no_reason(
    client: Client, mfa_login: Any
) -> None:
    client = mfa_login(client, UserFactory.create(staff=True))
    row = NoTinCountry.objects.get(country="BS")
    url = reverse("admin:vendors_notincountry_delete", args=[row.pk])
    assert client.post(url, {"post": "yes", "reason": ""}).status_code == 403
    assert NoTinCountry.objects.filter(country="BS").exists()


def test_the_engine_does_not_import_django_or_apps() -> None:
    tree = ast.parse(Path(engine.__file__).read_text())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    pure = {"apps.core.countries", "apps.core.text"}  # no ORM: plain helpers
    orm_django = ("django.db", "django.apps", "django.conf", "django.contrib")
    assert not [m for m in imported if m.startswith(orm_django)]
    assert not [m for m in imported if m.startswith("apps") and m not in pure]


def test_the_engine_imports_with_django_unconfigured() -> None:
    code = (
        "import sys; import apps.vendors.engine; "
        "assert not {'django.db', 'django.conf'} & set(sys.modules)"
    )
    result = subprocess.run(  # noqa: S603 - fixed argv, no user input
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=Path(engine.__file__).parents[2],
    )
    assert result.returncode == 0, result.stderr


def test_staff_add_and_remove_a_country_in_the_admin_and_it_is_audited(
    client: Client, superuser: User, mfa_login: Any
) -> None:
    staff: Client = mfa_login(client, superuser)
    response = staff.post(
        reverse("admin:vendors_notincountry_add"), {"country": "NZ", "reason": REASON}
    )
    assert response.status_code == 302
    assert "NZ" in no_tin_countries()
    assert AuditLog.objects.filter(action=NO_TIN_ADDED, target_key="NZ").exists()

    row = NoTinCountry.objects.get(country="NZ")
    staff.post(
        reverse("admin:vendors_notincountry_delete", args=[row.pk]),
        {"post": "yes", "reason": "Added by mistake."},
    )
    assert "NZ" not in no_tin_countries()
    assert AuditLog.objects.filter(action=NO_TIN_REMOVED, target_key="NZ").exists()


def test_a_listed_country_in_the_factory_row_counts_like_any_other() -> None:
    row = NoTinCountryFactory.create(country="NZ")
    assert row.country in no_tin_countries()


def test_a_removal_in_the_admin_needs_a_reason_and_records_it(
    client: Client, superuser: User, mfa_login: Any
) -> None:
    staff: Client = mfa_login(client, superuser)
    url = reverse(
        "admin:vendors_notincountry_delete", args=[NoTinCountry.objects.get(country="BS").pk]
    )
    assert b'name="reason"' in staff.get(url).content
    for reason in ("", "   "):
        assert staff.post(url, {"post": "yes", "reason": reason}).status_code == 302
        assert "BS" in no_tin_countries()
        assert not AuditLog.objects.filter(action=NO_TIN_REMOVED).exists()
    staff.post(url, {"post": "yes", "reason": "Accountant says it issues TINs."})
    assert "BS" not in no_tin_countries()
    removed = AuditLog.objects.get(action=NO_TIN_REMOVED, target_key="BS")
    assert (removed.before or {})["removal_reason"] == "Accountant says it issues TINs."


def test_the_admin_has_no_bulk_delete(client: Client, superuser: User, mfa_login: Any) -> None:
    staff: Client = mfa_login(client, superuser)
    response = staff.get(reverse("admin:vendors_notincountry_changelist"))
    assert b"delete_selected" not in response.content


def test_a_removal_counts_the_vendors_it_blocks() -> None:
    no_tin_vendor("BS")
    changes = remove_no_tin_country("BS", reason=REASON, ctx=CTX, dry_run=True)
    assert changes.changes[0].before["vendors_without_tin"] == 1  # type: ignore[index]


def test_a_lost_add_race_is_a_validation_error(monkeypatch: pytest.MonkeyPatch) -> None:
    real_clean = NoTinCountry.full_clean
    monkeypatch.setattr(NoTinCountry, "full_clean", lambda self, **kw: None)
    with pytest.raises(ValidationError):
        add_no_tin_country("BS", reason=REASON, ctx=CTX)  # unique violation at save
    monkeypatch.setattr(NoTinCountry, "full_clean", real_clean)
