"""CMP.13 (spec 11): staff get a flag when an admitted vendor moves out of the allowed set.

The gate runs at the VEN.5 activation. Afterwards the establishment can change in the VEN.12
identity: when an active (or suspended) vendor's registered office or permanent establishment
moves out of `PlatformOperator.allowed_vendor_countries`, nothing is suspended; the save writes
a `vendor.establishment_outside_allowed` audit entry, the admin warns, and a nightly job lists
the active vendors outside the set. Status changes to active record the establishment.
"""

import logging
from collections.abc import Callable
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.core.tasks import schedules
from apps.vendors import services, tasks
from apps.vendors.models import Vendor
from apps.vendors.services import ESTABLISHMENT_FLAGGED, change_status, save_vendor
from apps.vendors.tests.factories import ContractAcceptanceFactory, VendorFactory
from apps.vendors.tests.fakes import seed_legal_requirements
from apps.vendors.tests.integration.test_legal_admin import change_form

pytestmark = pytest.mark.django_db

STAFF_CTX = AuditContext(actor_id=1, actor_label="staff:reviewer", source="admin", ip="10.0.0.1")

MOVES_OUT = [
    pytest.param({"registered_country": "CN"}, ("CN", "IT"), id="registered-office-to-CN"),
    pytest.param({"establishment_country": ""}, ("CH", ""), id="loses-eu-establishment"),
]


def admitted_vendor(status: str = Vendor.Status.ACTIVE) -> Vendor:
    """A Swiss company admitted through its permanent establishment in Italy."""
    return VendorFactory.create(status=status, non_eu_company=True, establishment_country="IT")


def flag_changes(changeset: Any) -> list[Any]:
    return [
        change
        for change in changeset.changes
        if change.after and change.after.get("establishment_allowed") is False
    ]


@pytest.mark.parametrize(("edit", "place"), MOVES_OUT)
@pytest.mark.parametrize("status", [Vendor.Status.ACTIVE, Vendor.Status.SUSPENDED])
def test_moving_out_of_the_set_flags_the_vendor_for_staff(
    edit: dict[str, str], place: tuple[str, str], status: str
) -> None:
    vendor = admitted_vendor(status)
    for name, value in edit.items():
        setattr(vendor, name, value)

    preview = save_vendor(vendor, ctx=STAFF_CTX, dry_run=True)
    assert len(flag_changes(preview)) == 1
    assert not AuditLog.objects.exists()

    changeset = save_vendor(vendor, ctx=STAFF_CTX)
    [flag] = flag_changes(changeset)
    assert flag.key == str(vendor.public_id)
    assert flag.before["establishment_allowed"] is True
    assert (flag.after["registered_office"], flag.after["permanent_establishment"]) == place

    entry = AuditLog.objects.get(action=ESTABLISHMENT_FLAGGED)
    assert entry.target_key == str(vendor.public_id)
    assert entry.before["permanent_establishment"] == "IT"
    assert entry.after["establishment_allowed"] is False
    assert entry.after["registered_office"] == place[0]
    assert AuditLog.objects.filter(action="vendor.updated").count() == 1
    vendor.refresh_from_db()
    assert vendor.status == status  # staff decide: nobody is suspended


def test_moving_within_the_set_raises_no_flag() -> None:
    vendor = admitted_vendor()
    vendor.establishment_country = "DE"
    changeset = save_vendor(vendor, ctx=STAFF_CTX)
    assert flag_changes(changeset) == []
    assert not AuditLog.objects.filter(action=ESTABLISHMENT_FLAGGED).exists()


def test_a_vendor_not_yet_admitted_is_left_to_the_activation_gate() -> None:
    vendor = admitted_vendor(Vendor.Status.ONBOARDING)
    vendor.establishment_country = ""
    save_vendor(vendor, ctx=STAFF_CTX)
    assert not AuditLog.objects.filter(action=ESTABLISHMENT_FLAGGED).exists()


def test_the_flag_is_logged_as_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    vendor = admitted_vendor()
    vendor.establishment_country = ""
    with caplog.at_level(logging.WARNING, logger="apps.vendors"):
        save_vendor(vendor, ctx=STAFF_CTX)
    [warning] = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert str(vendor.public_id) in warning.getMessage()


def test_staff_saving_the_vendor_in_the_admin_are_warned(
    client: Client, superuser: User, mfa_login: Callable[[Client, User], Client]
) -> None:
    seed_legal_requirements()
    vendor = admitted_vendor()
    staff = mfa_login(client, superuser)
    url = reverse("admin:vendors_vendor_change", args=[vendor.pk])
    response = staff.post(url, change_form(vendor, establishment_country=""), follow=True)
    assert response.status_code == 200
    assert "outside the allowed vendor countries" in response.content.decode()
    assert AuditLog.objects.filter(action=ESTABLISHMENT_FLAGGED).exists()
    assert Vendor.objects.get(pk=vendor.pk).status == Vendor.Status.ACTIVE


# --- The status change to active records the establishment (minors 2 and 3)


def test_the_activation_audit_records_how_the_vendor_qualified() -> None:
    vendor = VendorFactory.create(
        status=Vendor.Status.ONBOARDING,
        country="CN",
        registered_country="CN",
        establishment_country="IT",
    )
    ContractAcceptanceFactory.create(vendor=vendor)
    change_status(vendor, Vendor.Status.ACTIVE, ctx=STAFF_CTX)
    entry = AuditLog.objects.get(action="vendor.status_changed")
    assert entry.after["registered_office"] == "CN"
    assert entry.after["permanent_establishment"] == "IT"
    assert entry.after["establishment_allowed"] is True


def test_reinstating_a_vendor_outside_the_set_is_allowed_and_recorded() -> None:
    vendor = VendorFactory.create(
        status=Vendor.Status.SUSPENDED, country="RS", registered_country="RS"
    )
    for dry_run in (True, False):
        changeset = change_status(vendor, Vendor.Status.ACTIVE, ctx=STAFF_CTX, dry_run=dry_run)
        assert changeset.changes[0].after["establishment_allowed"] is False
    entry = AuditLog.objects.get(action="vendor.status_changed")
    assert entry.after["establishment_allowed"] is False
    assert entry.after["registered_office"] == "RS"
    assert Vendor.objects.get(pk=vendor.pk).status == Vendor.Status.ACTIVE


def test_staff_reinstating_a_vendor_outside_the_set_are_warned(
    client: Client, superuser: User, mfa_login: Callable[[Client, User], Client]
) -> None:
    vendor = VendorFactory.create(
        status=Vendor.Status.SUSPENDED, country="RS", registered_country="RS"
    )
    staff = mfa_login(client, superuser)
    response = staff.post(
        reverse("admin:vendors_vendor_changelist"),
        {"action": "move_to_active", "_selected_action": [vendor.pk]},
        follow=True,
    )
    assert response.status_code == 200
    assert "outside the allowed vendor countries" in response.content.decode()
    assert Vendor.objects.get(pk=vendor.pk).status == Vendor.Status.ACTIVE


# --- Nightly re-check (ARC.9)


def test_the_nightly_job_logs_each_active_vendor_outside_the_set(
    caplog: pytest.LogCaptureFixture,
) -> None:
    outside = VendorFactory.create(status=Vendor.Status.ACTIVE, country="RS")
    VendorFactory.create(status=Vendor.Status.ACTIVE, italian_company=True)
    with caplog.at_level(logging.WARNING, logger="apps.vendors"):
        tasks.check_establishments.func(timestamp=0)
    [warning] = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert str(outside.public_id) in warning.getMessage()
    assert services.report_vendors_outside_allowed() == 1


def test_the_establishment_job_runs_every_night() -> None:
    names = {schedule.task_name for schedule in schedules()}
    assert "vendors.check_establishments" in names
