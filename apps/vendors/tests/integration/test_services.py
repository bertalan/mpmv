"""VEN.1: the status transition service refuses invalid moves and audits valid ones."""

import pytest

from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.vendors.engine import InvalidTransition
from apps.vendors.models import Vendor
from apps.vendors.services import change_status
from apps.vendors.tests.factories import ContractAcceptanceFactory, VendorFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:reviewer", source="admin", ip="127.0.0.1")


def test_valid_transition_updates_the_status_and_writes_the_audit_log() -> None:
    vendor = VendorFactory.create()
    changes = change_status(vendor, Vendor.Status.ONBOARDING, ctx=CTX)

    assert changes.summary() == {"create": 0, "update": 1, "delete": 0}
    assert vendor.status == Vendor.Status.ONBOARDING
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.ONBOARDING

    entry = AuditLog.objects.get()
    assert entry.action == "vendor.status_changed"
    assert entry.target_model == "vendors.vendor"
    assert entry.target_key == str(vendor.public_id)
    assert entry.before == {"status": "applicant", "slug": vendor.slug}
    assert entry.after == {"status": "onboarding", "slug": vendor.slug}
    assert entry.actor_label == "staff:reviewer"
    assert entry.source == "admin"


def test_full_lifecycle_writes_one_audit_row_per_step() -> None:
    vendor = VendorFactory.create()
    ContractAcceptanceFactory.create(vendor=vendor)  # activation needs the contract (VEN.5)
    for target in ("onboarding", "active", "suspended", "active", "closed"):
        change_status(vendor, target, ctx=CTX)
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.CLOSED
    assert AuditLog.objects.filter(action="vendor.status_changed").count() == 5


def test_audit_key_survives_a_slug_change_and_is_not_inherited_by_a_slug_reuse() -> None:
    vendor = VendorFactory.create(display_name="Vendor A")
    ContractAcceptanceFactory.create(vendor=vendor)  # activation needs the contract (VEN.5)
    change_status(vendor, Vendor.Status.ONBOARDING, ctx=CTX)
    vendor.slug = "vendor-a-renamed"
    vendor.save()
    change_status(vendor, Vendor.Status.ACTIVE, ctx=CTX)

    other = VendorFactory.create(display_name="Vendor A")  # takes the freed slug "vendor-a"
    assert other.slug == "vendor-a"
    change_status(other, Vendor.Status.ONBOARDING, ctx=CTX)

    history = AuditLog.objects.filter(target_model="vendors.vendor")
    assert history.filter(target_key=str(vendor.public_id)).count() == 2
    assert history.filter(target_key=str(other.public_id)).count() == 1


def test_invalid_transition_is_refused_and_nothing_changes() -> None:
    vendor = VendorFactory.create()
    with pytest.raises(InvalidTransition):
        change_status(vendor, Vendor.Status.ACTIVE, ctx=CTX)
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.APPLICANT
    assert AuditLog.objects.count() == 0


def test_closed_vendor_cannot_be_reopened() -> None:
    vendor = VendorFactory.create(status=Vendor.Status.CLOSED)
    with pytest.raises(InvalidTransition):
        change_status(vendor, Vendor.Status.ACTIVE, ctx=CTX)
    assert AuditLog.objects.count() == 0


def test_transition_is_checked_against_the_stored_status_not_a_stale_instance() -> None:
    vendor = VendorFactory.create()
    stale = Vendor.objects.get(pk=vendor.pk)
    change_status(vendor, Vendor.Status.CLOSED, ctx=CTX)
    with pytest.raises(InvalidTransition):
        change_status(stale, Vendor.Status.ONBOARDING, ctx=CTX)
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.CLOSED


def test_dry_run_returns_the_change_without_applying_or_auditing() -> None:
    vendor = VendorFactory.create()
    changes = change_status(vendor, Vendor.Status.ONBOARDING, ctx=CTX, dry_run=True)
    assert changes.to_dict()["changes"] == [
        {
            "op": "update",
            "model": "vendors.vendor",
            "key": str(vendor.public_id),
            "fields": {"status": ["applicant", "onboarding"]},
        }
    ]
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.APPLICANT
    assert AuditLog.objects.count() == 0


def test_dry_run_still_refuses_invalid_transitions() -> None:
    with pytest.raises(InvalidTransition):
        change_status(VendorFactory.create(), Vendor.Status.SUSPENDED, ctx=CTX, dry_run=True)
