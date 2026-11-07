"""SEC.4: staff edits of a vendor's legal, tax and contact data are audited with before/after."""

import pytest

from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.vendors.models import Vendor, VendorMember
from apps.vendors.services import delete_vendor, save_vendor
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:reviewer", source="admin", ip="127.0.0.1")


def test_creating_a_vendor_is_audited_with_its_generated_slug() -> None:
    vendor = VendorFactory.build(display_name="Vendor A", slug="")
    changes = save_vendor(vendor, ctx=CTX)

    assert changes.summary() == {"create": 1, "update": 0, "delete": 0}
    stored = Vendor.objects.get()
    entry = AuditLog.objects.get()
    assert entry.action == "vendor.created"
    assert entry.target_model == "vendors.vendor"
    assert entry.target_key == str(stored.public_id)
    assert entry.vendor_key == str(stored.public_id)
    assert entry.before is None
    assert entry.after is not None
    assert entry.after["slug"] == stored.slug == "vendor-a"
    assert entry.after["vat_number"] == stored.vat_number


def test_a_field_change_is_audited_with_only_what_changed() -> None:
    vendor = VendorFactory.create(vat_number="IT00000000001", contact_email="a@example.com")
    vendor.vat_number = "IT00000000002"
    changes = save_vendor(vendor, ctx=CTX)

    assert [change.fields for change in changes.changes] == [
        {"vat_number": ("IT00000000001", "IT00000000002")}
    ]
    entry = AuditLog.objects.get()
    assert entry.action == "vendor.updated"
    assert entry.before is not None
    assert entry.after is not None
    assert (entry.before["vat_number"], entry.after["vat_number"]) == (
        "IT00000000001",
        "IT00000000002",
    )
    assert entry.vendor_key == str(vendor.public_id)
    vendor.refresh_from_db()
    assert vendor.vat_number == "IT00000000002"


def test_the_before_values_come_from_the_database_not_the_edited_instance() -> None:
    vendor = VendorFactory.create(legal_name="Vendor A S.r.l.")
    edited = Vendor.objects.get(pk=vendor.pk)
    edited.legal_name = "Vendor A Holding S.r.l."
    save_vendor(edited, ctx=CTX)
    entry = AuditLog.objects.get()
    assert entry.before is not None
    assert entry.before["legal_name"] == "Vendor A S.r.l."


def test_saving_without_changes_writes_no_audit_entry() -> None:
    vendor = VendorFactory.create()
    assert save_vendor(vendor, ctx=CTX).is_empty
    assert not AuditLog.objects.exists()


def test_a_vendor_save_dry_run_writes_nothing() -> None:
    vendor = VendorFactory.create(phone="")
    vendor.phone = "+39 000 0000000"
    changes = save_vendor(vendor, ctx=CTX, dry_run=True)
    assert changes.summary()["update"] == 1
    vendor.refresh_from_db()
    assert vendor.phone == ""
    assert not AuditLog.objects.exists()


def test_deleting_a_vendor_audits_it_and_its_memberships() -> None:
    member = VendorMemberFactory.create()
    vendor = member.vendor
    changes = delete_vendor(vendor, ctx=CTX)

    assert changes.summary() == {"create": 0, "update": 0, "delete": 2}
    assert not Vendor.objects.exists()
    assert not VendorMember.objects.exists()
    entries = AuditLog.objects.filter(action="vendor.deleted")
    assert {entry.target_model for entry in entries} == {"vendors.vendor", "vendors.vendormember"}
    assert {entry.vendor_key for entry in entries} == {str(vendor.public_id)}


def test_a_vendor_delete_dry_run_writes_nothing() -> None:
    vendor = VendorFactory.create()
    delete_vendor(vendor, ctx=CTX, dry_run=True)
    assert Vendor.objects.exists()
    assert not AuditLog.objects.exists()
