"""VEN.1: the status transition service refuses invalid moves and audits valid ones."""

import pytest

from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.vendors.engine import InvalidTransition
from apps.vendors.models import Vendor
from apps.vendors.services import change_status

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:reviewer", source="admin", ip="127.0.0.1")


def make_vendor(status: str = Vendor.Status.APPLICANT) -> Vendor:
    return Vendor.objects.create(
        legal_name="Vendor A S.r.l.",
        display_name="Vendor A",
        country="IT",
        contact_email="vendor-a@example.com",
        status=status,
    )


def test_valid_transition_updates_the_status_and_writes_the_audit_log() -> None:
    vendor = make_vendor()
    changes = change_status(vendor, Vendor.Status.ONBOARDING, ctx=CTX)

    assert changes.summary() == {"create": 0, "update": 1, "delete": 0}
    assert vendor.status == Vendor.Status.ONBOARDING
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.ONBOARDING

    entry = AuditLog.objects.get()
    assert entry.action == "vendor.status_changed"
    assert entry.target_model == "vendors.vendor"
    assert entry.target_key == "vendor-a"
    assert entry.before == {"status": "applicant"}
    assert entry.after == {"status": "onboarding"}
    assert entry.actor_label == "staff:reviewer"
    assert entry.source == "admin"


def test_full_lifecycle_writes_one_audit_row_per_step() -> None:
    vendor = make_vendor()
    for target in ("onboarding", "active", "suspended", "active", "closed"):
        change_status(vendor, target, ctx=CTX)
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.CLOSED
    assert AuditLog.objects.filter(action="vendor.status_changed").count() == 5


def test_invalid_transition_is_refused_and_nothing_changes() -> None:
    vendor = make_vendor()
    with pytest.raises(InvalidTransition):
        change_status(vendor, Vendor.Status.ACTIVE, ctx=CTX)
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.APPLICANT
    assert AuditLog.objects.count() == 0


def test_closed_vendor_cannot_be_reopened() -> None:
    vendor = make_vendor(status=Vendor.Status.CLOSED)
    with pytest.raises(InvalidTransition):
        change_status(vendor, Vendor.Status.ACTIVE, ctx=CTX)
    assert AuditLog.objects.count() == 0


def test_transition_is_checked_against_the_stored_status_not_a_stale_instance() -> None:
    vendor = make_vendor()
    stale = Vendor.objects.get(pk=vendor.pk)
    change_status(vendor, Vendor.Status.CLOSED, ctx=CTX)
    with pytest.raises(InvalidTransition):
        change_status(stale, Vendor.Status.ONBOARDING, ctx=CTX)
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.CLOSED


def test_dry_run_returns_the_change_without_applying_or_auditing() -> None:
    vendor = make_vendor()
    changes = change_status(vendor, Vendor.Status.ONBOARDING, ctx=CTX, dry_run=True)
    assert changes.to_dict()["changes"] == [
        {
            "op": "update",
            "model": "vendors.vendor",
            "key": "vendor-a",
            "fields": {"status": ["applicant", "onboarding"]},
        }
    ]
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.APPLICANT
    assert AuditLog.objects.count() == 0


def test_dry_run_still_refuses_invalid_transitions() -> None:
    with pytest.raises(InvalidTransition):
        change_status(make_vendor(), Vendor.Status.SUSPENDED, ctx=CTX, dry_run=True)
