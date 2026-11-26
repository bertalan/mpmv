"""VEN.20: audited services for a vendor's holiday and closure schedule (spec 04, case 10)."""

import datetime as dt
from typing import Any

import pytest
from django.core.exceptions import ValidationError

from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.vendors.models import VendorClosure
from apps.vendors.services import add_closure, delete_closure, update_closure
from apps.vendors.tests.factories import VendorClosureFactory, VendorFactory

pytestmark = pytest.mark.django_db

D = dt.date
TODAY = D(2027, 6, 1)
CTX = AuditContext(actor_id=1, actor_label="vendor:owner", source="vendor_area", ip="127.0.0.1")
ADDED = "vendors.closure.added"
CHANGED = "vendors.closure.changed"
REMOVED = "vendors.closure.removed"


def test_adding_a_closure_saves_it_at_once_with_one_audit_entry() -> None:
    vendor = VendorFactory.create()
    changes = add_closure(vendor, D(2027, 8, 1), D(2027, 8, 15), ctx=CTX, today=TODAY)
    assert changes.summary() == {"create": 1, "update": 0, "delete": 0}
    closure = VendorClosure.objects.get(vendor=vendor)
    assert (closure.start_date, closure.end_date) == (D(2027, 8, 1), D(2027, 8, 15))
    assert closure.repeats_yearly is False
    assert closure.notice_days == 14
    entry = AuditLog.objects.get(action=ADDED)
    assert entry.actor_label == "vendor:owner"
    assert entry.source == "vendor_area"
    assert entry.vendor_key == str(vendor.public_id)
    assert entry.after is not None
    assert entry.after["start_date"] == "2027-08-01"


def test_dry_run_returns_the_change_and_writes_nothing() -> None:
    vendor = VendorFactory.create()
    changes = add_closure(vendor, D(2027, 8, 1), D(2027, 8, 15), ctx=CTX, today=TODAY, dry_run=True)
    assert changes.summary()["create"] == 1
    assert not VendorClosure.objects.exists()
    assert not AuditLog.objects.filter(action=ADDED).exists()


def test_an_invalid_range_is_refused_even_on_dry_run() -> None:
    vendor = VendorFactory.create()
    for dry_run in (False, True):
        with pytest.raises(ValidationError) as caught:
            add_closure(
                vendor,
                D(2028, 2, 20),
                D(2028, 2, 29),
                repeats_yearly=True,
                ctx=CTX,
                today=TODAY,
                dry_run=dry_run,
            )
        assert "end_date" in caught.value.message_dict
    assert not VendorClosure.objects.exists()


def test_a_range_that_has_already_ended_cannot_be_added() -> None:
    vendor = VendorFactory.create()
    with pytest.raises(ValidationError) as caught:
        add_closure(vendor, D(2027, 5, 1), D(2027, 5, 10), ctx=CTX, today=TODAY)
    assert "end_date" in caught.value.message_dict


def test_update_audits_before_and_after() -> None:
    vendor = VendorFactory.create()
    add_closure(vendor, D(2027, 8, 1), D(2027, 8, 15), ctx=CTX, today=TODAY)
    closure = VendorClosure.objects.get(vendor=vendor)
    changes = update_closure(closure, end_date=D(2027, 8, 20), notice_days=30, ctx=CTX, today=TODAY)
    assert changes.summary()["update"] == 1
    closure.refresh_from_db()
    assert closure.end_date == D(2027, 8, 20)
    assert closure.notice_days == 30
    entry = AuditLog.objects.get(action=CHANGED)
    assert entry.before is not None
    assert entry.after is not None
    assert entry.before["end_date"] == "2027-08-15"
    assert entry.after["end_date"] == "2027-08-20"


def test_update_dry_run_keeps_the_row() -> None:
    vendor = VendorFactory.create()
    add_closure(vendor, D(2027, 8, 1), D(2027, 8, 15), ctx=CTX, today=TODAY)
    closure = VendorClosure.objects.get(vendor=vendor)
    update_closure(closure, end_date=D(2027, 8, 20), ctx=CTX, today=TODAY, dry_run=True)
    closure.refresh_from_db()
    assert closure.end_date == D(2027, 8, 15)
    assert not AuditLog.objects.filter(action=CHANGED).exists()


def test_an_ended_range_stays_as_history() -> None:
    vendor = VendorFactory.create()
    add_closure(vendor, D(2027, 5, 20), D(2027, 6, 10), ctx=CTX, today=D(2027, 5, 25))
    closure = VendorClosure.objects.get(vendor=vendor)
    later = D(2027, 6, 11)
    with pytest.raises(ValidationError):
        update_closure(closure, notice_days=1, ctx=CTX, today=later)
    with pytest.raises(ValidationError):
        delete_closure(closure, ctx=CTX, today=later)
    assert VendorClosure.objects.filter(pk=closure.pk).exists()


def test_a_yearly_range_is_never_history() -> None:
    vendor = VendorFactory.create()
    add_closure(
        vendor, D(2027, 5, 20), D(2027, 6, 10), repeats_yearly=True, ctx=CTX, today=D(2027, 5, 25)
    )
    closure = VendorClosure.objects.get(vendor=vendor)
    update_closure(closure, notice_days=3, ctx=CTX, today=D(2028, 1, 1))
    closure.refresh_from_db()
    assert closure.notice_days == 3


def test_delete_audits_and_dry_run_keeps_the_row() -> None:
    vendor = VendorFactory.create()
    add_closure(vendor, D(2027, 8, 1), D(2027, 8, 15), ctx=CTX, today=TODAY)
    closure = VendorClosure.objects.get(vendor=vendor)
    delete_closure(closure, ctx=CTX, today=TODAY, dry_run=True)
    assert VendorClosure.objects.filter(pk=closure.pk).exists()
    changes = delete_closure(closure, ctx=CTX, today=TODAY)
    assert changes.summary()["delete"] == 1
    assert not VendorClosure.objects.exists()
    assert AuditLog.objects.filter(action=REMOVED).count() == 1


def test_overlapping_ranges_are_allowed() -> None:
    vendor = VendorFactory.create()
    add_closure(vendor, D(2027, 8, 1), D(2027, 8, 15), ctx=CTX, today=TODAY)
    add_closure(vendor, D(2027, 8, 10), D(2027, 8, 31), ctx=CTX, today=TODAY)
    assert VendorClosure.objects.filter(vendor=vendor).count() == 2


def test_validation_messages_are_translated() -> None:
    from django.utils import translation

    vendor = VendorFactory.create()
    with translation.override("it"), pytest.raises(ValidationError) as caught:
        add_closure(vendor, D(2027, 8, 2), D(2027, 8, 1), ctx=CTX, today=TODAY)
    assert caught.value.message_dict["end_date"] == [
        "La data di fine non può precedere la data di inizio."
    ]


def test_factory_row_can_be_updated_through_the_service() -> None:
    closure = VendorClosureFactory.create()
    update_closure(closure, notice_days=7, ctx=CTX, today=TODAY)
    closure.refresh_from_db()
    assert closure.notice_days == 7


def test_a_change_moves_the_cache_generation_and_a_dry_run_does_not(
    django_capture_on_commit_callbacks: Any,
) -> None:
    from apps.vendors.closure_cache import generation

    vendor = VendorFactory.create()
    before = generation(vendor.pk)
    with django_capture_on_commit_callbacks(execute=True):
        add_closure(vendor, D(2027, 8, 1), D(2027, 8, 15), ctx=CTX, today=TODAY, dry_run=True)
    assert generation(vendor.pk) == before
    with django_capture_on_commit_callbacks(execute=True):
        add_closure(vendor, D(2027, 8, 1), D(2027, 8, 15), ctx=CTX, today=TODAY)
    closure = VendorClosure.objects.get(vendor=vendor)
    assert generation(vendor.pk) == before + 1
    with django_capture_on_commit_callbacks(execute=True):
        update_closure(closure, notice_days=3, ctx=CTX, today=TODAY)
    assert generation(vendor.pk) == before + 2
    with django_capture_on_commit_callbacks(execute=True):
        delete_closure(closure, ctx=CTX, today=TODAY)
    assert generation(vendor.pk) == before + 3
