"""CAT.14: a vendor's versioned opt-in to other vendors' suggestions (owner decision Q-32).

`CrossVendorConsent` is a versioned, append-only, audited vendor setting (spec 04 §4.2): no row
means opted out; a change is a new version, never an edit.
"""

import datetime as dt
from collections.abc import Callable

import pytest
from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.vendors.models import CrossVendorConsent
from apps.vendors.services import (
    current_cross_vendor_consent,
    in_suggestions_allowed,
    set_cross_vendor_consent,
)
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:ops", source="admin", ip="127.0.0.1")
ACTION = "vendors.cross_vendor_consent.set"


def test_a_vendor_without_a_consent_row_is_opted_out() -> None:
    vendor = VendorFactory.create()
    assert current_cross_vendor_consent(vendor) is None
    assert not in_suggestions_allowed(vendor)


def test_opting_in_creates_an_audited_version() -> None:
    vendor = VendorFactory.create()
    changes = set_cross_vendor_consent(vendor, in_suggestions=True, ctx=CTX)
    assert changes.summary() == {"create": 1, "update": 0, "delete": 0}
    consent = CrossVendorConsent.objects.get(vendor=vendor)
    assert consent.in_suggestions is True
    assert consent.in_bundles is False
    assert consent.set_by == "staff:ops"
    assert in_suggestions_allowed(vendor)

    entry = AuditLog.objects.get(action=ACTION)
    assert entry.target_model == "vendors.crossvendorconsent"
    assert entry.before is None
    assert entry.after is not None
    assert entry.after["in_suggestions"] is True
    assert entry.after["vendor"] == str(vendor.public_id)


def test_a_change_is_a_new_version_and_the_old_one_is_kept() -> None:
    vendor = VendorFactory.create()
    start = timezone.now() - dt.timedelta(days=2)
    set_cross_vendor_consent(vendor, in_suggestions=True, valid_from=start, ctx=CTX)
    set_cross_vendor_consent(vendor, in_suggestions=False, ctx=CTX)

    versions = list(CrossVendorConsent.objects.filter(vendor=vendor).order_by("valid_from"))
    assert [v.in_suggestions for v in versions] == [True, False]
    assert not in_suggestions_allowed(vendor)
    assert in_suggestions_allowed(vendor, at=start + dt.timedelta(days=1))

    withdrawal = AuditLog.objects.filter(action=ACTION).order_by("id").last()
    assert withdrawal is not None
    assert withdrawal.before is not None
    assert withdrawal.before["in_suggestions"] is True
    assert withdrawal.after is not None
    assert withdrawal.after["in_suggestions"] is False


def test_an_unchanged_flag_keeps_its_current_value() -> None:
    vendor = VendorFactory.create()
    start = timezone.now() - dt.timedelta(days=1)
    set_cross_vendor_consent(vendor, in_suggestions=True, valid_from=start, ctx=CTX)
    set_cross_vendor_consent(vendor, in_bundles=True, ctx=CTX)
    consent = current_cross_vendor_consent(vendor)
    assert consent is not None
    assert (consent.in_suggestions, consent.in_bundles) == (True, True)


def test_a_version_cannot_start_before_the_latest_one() -> None:
    vendor = VendorFactory.create()
    now = timezone.now()
    set_cross_vendor_consent(vendor, in_suggestions=True, valid_from=now, ctx=CTX)
    with pytest.raises(ValidationError):
        set_cross_vendor_consent(
            vendor, in_suggestions=False, valid_from=now - dt.timedelta(hours=1), ctx=CTX
        )


def test_a_future_version_is_not_effective_yet() -> None:
    vendor = VendorFactory.create()
    later = timezone.now() + dt.timedelta(days=3)
    set_cross_vendor_consent(vendor, in_suggestions=True, valid_from=later, ctx=CTX)
    assert not in_suggestions_allowed(vendor)
    assert in_suggestions_allowed(vendor, at=later)


def test_dry_run_writes_nothing() -> None:
    vendor = VendorFactory.create()
    changes = set_cross_vendor_consent(vendor, in_suggestions=True, ctx=CTX, dry_run=True)
    assert changes.summary()["create"] == 1
    assert not CrossVendorConsent.objects.exists()
    assert not AuditLog.objects.filter(action=ACTION).exists()


def test_consent_versions_are_append_only_in_the_database() -> None:
    vendor = VendorFactory.create()
    set_cross_vendor_consent(vendor, in_suggestions=True, ctx=CTX)
    consent = CrossVendorConsent.objects.get()
    with pytest.raises(PermissionError):
        consent.save()
    rows = CrossVendorConsent.objects.filter(pk=consent.pk)
    with pytest.raises(DatabaseError, match="append-only"), transaction.atomic():
        rows.update(in_suggestions=False)
    with pytest.raises(DatabaseError, match="append-only"), transaction.atomic():
        rows.delete()
    assert rows.get().in_suggestions is True


# --- Admin: staff record a vendor's choice as a new version


@pytest.fixture
def staff_client(
    client: Client, superuser: User, mfa_login: Callable[[Client, User], Client]
) -> Client:
    return mfa_login(client, superuser)


def test_staff_record_a_new_version_in_the_admin(staff_client: Client) -> None:
    vendor = VendorFactory.create()
    response = staff_client.post(
        reverse("admin:vendors_crossvendorconsent_add"),
        {"vendor": str(vendor.pk), "in_suggestions": "on"},
    )
    assert response.status_code == 302, response.content.decode()[:2000]
    assert in_suggestions_allowed(vendor)
    assert AuditLog.objects.filter(action=ACTION).count() == 1


def test_a_consent_version_cannot_be_edited_or_deleted_in_the_admin(
    staff_client: Client,
) -> None:
    vendor = VendorFactory.create()
    set_cross_vendor_consent(vendor, in_suggestions=True, ctx=CTX)
    consent = CrossVendorConsent.objects.get()
    change_url = reverse("admin:vendors_crossvendorconsent_change", args=[consent.pk])
    staff_client.post(change_url, {"vendor": str(vendor.pk)})
    delete_url = reverse("admin:vendors_crossvendorconsent_delete", args=[consent.pk])
    assert staff_client.post(delete_url, {"post": "yes"}).status_code == 403
    consent.refresh_from_db()
    assert consent.in_suggestions is True
