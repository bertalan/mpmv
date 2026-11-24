"""VEN.41 (slice 1, owner decision 2026-11-22): the business-only seller flag is set by staff
only, through a service with `dry_run` and an audit entry, and it exempts nothing: the activation
gate (CMP.25) and the phone rule (CMP.41) treat a flagged vendor like any other."""

from typing import Any

import pytest
from django.core.exceptions import PermissionDenied

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.vendors import engine
from apps.vendors.models import LegalFieldRequirement, Vendor
from apps.vendors.services import (
    BUSINESS_ONLY_CHANGED,
    change_status,
    platform_duties_missing,
    save_vendor,
    sells_to_consumers,
    set_business_only,
)
from apps.vendors.tests.factories import (
    ContractAcceptanceFactory,
    VendorFactory,
    VendorMemberFactory,
)
from apps.vendors.tests.fakes import seed_legal_requirements

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:reviewer", source="admin", ip="10.0.0.1")
REASON = "VAT number checked in VIES, the seller is a company"


def staff_user(**kwargs: Any) -> User:
    return UserFactory.create(staff=True, permissions=["vendors.change_vendor"], **kwargs)


def test_the_flag_is_off_by_default() -> None:
    assert VendorFactory.create().business_only is False


def test_staff_set_the_flag_and_the_change_is_audited_with_the_reason() -> None:
    vendor = VendorFactory.create()
    changes = set_business_only(vendor, True, actor=staff_user(), reason=REASON, ctx=CTX)
    assert not changes.is_empty
    vendor.refresh_from_db()
    assert vendor.business_only is True
    entry = AuditLog.objects.get(action=BUSINESS_ONLY_CHANGED)
    assert entry.before is not None
    assert entry.after is not None
    assert entry.before["business_only"] is False
    assert entry.after["business_only"] is True
    assert entry.after["reason"] == REASON
    assert entry.actor_label == CTX.actor_label


def test_staff_clear_the_flag() -> None:
    vendor = VendorFactory.create(business_only=True)
    set_business_only(vendor, False, actor=staff_user(), reason=REASON, ctx=CTX)
    vendor.refresh_from_db()
    assert vendor.business_only is False


def test_dry_run_changes_nothing_and_returns_the_changeset() -> None:
    vendor = VendorFactory.create()
    changes = set_business_only(
        vendor, True, actor=staff_user(), reason=REASON, ctx=CTX, dry_run=True
    )
    assert not changes.is_empty
    vendor.refresh_from_db()
    assert vendor.business_only is False
    assert not AuditLog.objects.filter(action=BUSINESS_ONLY_CHANGED).exists()


def test_setting_the_stored_value_again_is_a_no_op() -> None:
    vendor = VendorFactory.create(business_only=True)
    changes = set_business_only(vendor, True, actor=staff_user(), reason=REASON, ctx=CTX)
    assert changes.is_empty
    assert not AuditLog.objects.filter(action=BUSINESS_ONLY_CHANGED).exists()


@pytest.mark.parametrize("reason", ["", "   "])
def test_a_change_without_a_reason_is_refused(reason: str) -> None:
    vendor = VendorFactory.create()
    for dry_run in (True, False):
        with pytest.raises(engine.ReasonRequired):
            set_business_only(
                vendor, True, actor=staff_user(), reason=reason, ctx=CTX, dry_run=dry_run
            )
    vendor.refresh_from_db()
    assert vendor.business_only is False


@pytest.mark.parametrize(
    "make_actor",
    [
        pytest.param(lambda: UserFactory.create(), id="plain-user"),
        pytest.param(lambda: UserFactory.create(staff=True), id="staff-without-permission"),
        pytest.param(lambda: staff_user(is_active=False), id="inactive-staff"),
    ],
)
def test_only_staff_can_set_the_flag(make_actor: Any) -> None:
    vendor = VendorFactory.create()
    for dry_run in (True, False):
        with pytest.raises(PermissionDenied):
            set_business_only(
                vendor, True, actor=make_actor(), reason=REASON, ctx=CTX, dry_run=dry_run
            )
    vendor.refresh_from_db()
    assert vendor.business_only is False
    assert not AuditLog.objects.exists()


def test_the_vendors_own_members_cannot_set_the_flag() -> None:
    member = VendorMemberFactory.create()
    with pytest.raises(PermissionDenied):
        set_business_only(member.vendor, True, actor=member.user, reason=REASON, ctx=CTX)
    member.vendor.refresh_from_db()
    assert member.vendor.business_only is False


def test_saving_a_stale_vendor_never_undoes_the_flag() -> None:
    vendor = VendorFactory.create()
    stale = Vendor.objects.get(pk=vendor.pk)
    set_business_only(vendor, True, actor=staff_user(), reason=REASON, ctx=CTX)
    stale.display_name = "Renamed shop"
    save_vendor(stale, ctx=CTX)
    assert Vendor.objects.get(pk=vendor.pk).business_only is True


def test_the_flag_exempts_nothing_from_the_consumer_phone_rule() -> None:
    assert sells_to_consumers(VendorFactory.create(business_only=True))


def test_a_flagged_vendor_without_a_phone_still_fails_the_activation_gate() -> None:
    seed_legal_requirements()
    LegalFieldRequirement.objects.filter(field_name="phone").update(active=False)
    vendor = VendorFactory.create(
        status=Vendor.Status.ONBOARDING, eu_company=True, phone="", business_only=True
    )
    ContractAcceptanceFactory.create(vendor=vendor)
    assert platform_duties_missing(vendor) == ("phone",)
    with pytest.raises(engine.ActivationNotAllowed):
        change_status(vendor, engine.ACTIVE, ctx=CTX)
    assert Vendor.objects.get(pk=vendor.pk).status == Vendor.Status.ONBOARDING
