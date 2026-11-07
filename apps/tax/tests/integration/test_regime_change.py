"""TAX.2: regime change workflow. A vendor owner requests a new VAT regime, staff approve or
reject it; an approval adds the new VendorTaxProfile version and keeps the vendor's declared
fiscal regime (VEN.12) consistent with it, in one transaction, with a full audit trail."""

import datetime as dt
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.core.tests.factories import utc
from apps.tax import engine, services
from apps.tax.models import TaxRegimeChangeRequest, VendorTaxProfile
from apps.tax.tests.factories import TaxRegimeChangeRequestFactory, VendorTaxProfileFactory
from apps.vendors.engine import FLAT_RATE_REGIME
from apps.vendors.models import Vendor, VendorMember
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="user:owner", source="vendor", ip="127.0.0.1")
STAFF_CTX = AuditContext(actor_id=2, actor_label="staff:ops", source="admin", ip="127.0.0.1")
PAST = utc(2026, 1)  # before the test clock: a fiscal regime change may apply at once


def owner_of(vendor: Vendor) -> User:
    member: VendorMember = VendorMemberFactory.create(vendor=vendor)
    return member.user


def reviewer() -> User:
    user: User = UserFactory.create(staff=True)
    return user


def new_request(vendor: Vendor, **values: Any) -> TaxRegimeChangeRequest:
    values.setdefault("requested_by", owner_of(vendor))
    request: TaxRegimeChangeRequest = TaxRegimeChangeRequestFactory.build(vendor=vendor, **values)
    return request


def submitted(vendor: Vendor, **values: Any) -> TaxRegimeChangeRequest:
    request = new_request(vendor, **values)
    services.request_regime_change(request, ctx=CTX)
    return TaxRegimeChangeRequest.objects.get(pk=request.pk)


def actions() -> list[str]:
    return list(AuditLog.objects.order_by("id").values_list("action", flat=True))


# --- Vendor request


def test_an_owner_request_is_stored_pending_and_audited() -> None:
    vendor = VendorFactory.create(fiscal_regime="RF01")
    request = new_request(vendor)
    changeset = services.request_regime_change(request, ctx=CTX)

    stored = TaxRegimeChangeRequest.objects.get()
    assert stored.status == engine.PENDING
    assert stored.requested_by == request.requested_by
    assert stored.requested_at is not None
    assert changeset.summary() == {"create": 1, "update": 0, "delete": 0}
    entry = AuditLog.objects.get()
    assert entry.action == "tax.regime_change.requested"
    assert entry.target_model == "tax.taxregimechangerequest"
    assert entry.target_key == str(stored.public_id)
    assert entry.vendor_key == str(vendor.public_id)
    assert entry.after is not None
    assert entry.after["regime"] == "oss"
    assert entry.after["requested_by"] == str(request.requested_by.public_id)


def test_a_dry_run_request_writes_nothing() -> None:
    vendor = VendorFactory.create(fiscal_regime="RF01")
    changeset = services.request_regime_change(new_request(vendor), ctx=CTX, dry_run=True)
    assert changeset.summary()["create"] == 1
    assert TaxRegimeChangeRequest.objects.count() == 0
    assert AuditLog.objects.count() == 0


def test_vendor_staff_members_cannot_request_a_regime_change() -> None:
    vendor = VendorFactory.create(fiscal_regime="RF01")
    member = VendorMemberFactory.create(vendor=vendor, role=VendorMember.Role.STAFF)
    with pytest.raises(engine.RegimeChangeNotAllowed):
        services.request_regime_change(new_request(vendor, requested_by=member.user), ctx=CTX)
    assert TaxRegimeChangeRequest.objects.count() == 0


def test_users_outside_the_vendor_and_inactive_owners_cannot_request() -> None:
    vendor = VendorFactory.create(fiscal_regime="RF01")
    outsider = owner_of(VendorFactory.create())
    inactive = owner_of(vendor)
    User.objects.filter(pk=inactive.pk).update(is_active=False)
    inactive.refresh_from_db()
    for user in (outsider, inactive):
        with pytest.raises(engine.RegimeChangeNotAllowed):
            services.request_regime_change(new_request(vendor, requested_by=user), ctx=CTX)


def test_a_vendor_has_at_most_one_pending_request() -> None:
    vendor = VendorFactory.create(fiscal_regime="RF01")
    submitted(vendor)
    with pytest.raises(engine.PendingRequestExists):
        services.request_regime_change(new_request(vendor, regime="domestic_only"), ctx=CTX)
    assert TaxRegimeChangeRequest.objects.count() == 1


def test_a_request_inconsistent_with_the_fiscal_regime_is_refused() -> None:
    vendor = VendorFactory.create(fiscal_regime=FLAT_RATE_REGIME)
    with pytest.raises(ValidationError) as exc:
        services.request_regime_change(new_request(vendor), ctx=CTX)
    assert "fiscal_regime" in exc.value.message_dict


def test_a_request_starts_after_the_latest_profile() -> None:
    profile = VendorTaxProfileFactory.create(valid_from=utc(2027, 9))
    vendor = profile.vendor
    with pytest.raises(ValidationError) as exc:
        services.request_regime_change(new_request(vendor, effective_from=utc(2027, 9)), ctx=CTX)
    assert "effective_from" in exc.value.message_dict


def test_a_request_for_the_current_regime_is_refused() -> None:
    profile = VendorTaxProfileFactory.create(vendor__fiscal_regime="RF01", valid_from=utc(2026))
    with pytest.raises(ValidationError) as exc:
        services.request_regime_change(
            new_request(profile.vendor, regime="domestic_only", oss_registered_since=None),
            ctx=CTX,
        )
    assert "regime" in exc.value.message_dict


# --- Staff approval


def test_approval_adds_the_new_profile_version_and_closes_the_current_one() -> None:
    current = VendorTaxProfileFactory.create(vendor__fiscal_regime="RF01", valid_from=utc(2026))
    request = submitted(current.vendor)
    staff = reviewer()

    services.approve_regime_change(request, staff, reason="OSS number checked.", ctx=STAFF_CTX)

    current.refresh_from_db()
    assert current.valid_to == request.effective_from
    new = VendorTaxProfile.objects.get(valid_to__isnull=True)
    assert (new.regime, new.valid_from) == ("oss", request.effective_from)
    assert new.oss_registered_since == request.oss_registered_since
    assert new.approved_by == staff
    assert new.requested_by == request.requested_by
    request.refresh_from_db()
    assert request.status == engine.APPROVED
    assert request.profile == new
    assert request.reviewed_by == staff
    assert request.reviewed_at is not None
    assert request.review_reason == "OSS number checked."
    assert actions() == [
        "tax.regime_change.requested",
        "tax.vendor_tax_profile.configured",  # closes the current version
        "tax.vendor_tax_profile.configured",  # adds the new one
        "tax.regime_change.approved",
    ]
    approved = AuditLog.objects.get(action="tax.regime_change.approved")
    assert approved.before is not None
    assert approved.after is not None
    assert (approved.before["status"], approved.after["status"]) == ("pending", "approved")
    assert approved.after["profile"] == str(new.public_id)
    assert approved.vendor_key == str(current.vendor.public_id)


def test_joining_the_flat_rate_scheme_sets_the_fiscal_regime_in_the_same_approval() -> None:
    vendor = VendorFactory.create(fiscal_regime="RF01")
    request = submitted(
        vendor,
        regime="exempt",
        exempt_reason="flat_rate_forfettario",
        oss_registered_since=None,
        effective_from=PAST,
    )
    services.approve_regime_change(request, reviewer(), ctx=STAFF_CTX)

    vendor.refresh_from_db()
    assert vendor.fiscal_regime == FLAT_RATE_REGIME
    profile = VendorTaxProfile.objects.get(vendor=vendor)
    assert (profile.regime, profile.exempt_reason) == ("exempt", "flat_rate_forfettario")
    entry = AuditLog.objects.get(action="vendor.updated")
    assert entry.before is not None
    assert entry.after is not None
    assert (entry.before["fiscal_regime"], entry.after["fiscal_regime"]) == ("RF01", "RF19")


def test_leaving_the_flat_rate_scheme_sets_the_requested_fiscal_regime() -> None:
    current = VendorTaxProfileFactory.create(flat_rate=True, valid_from=utc(2025))
    request = submitted(
        current.vendor,
        regime="domestic_only",
        oss_registered_since=None,
        fiscal_regime="RF01",
        effective_from=PAST,
    )
    services.approve_regime_change(request, reviewer(), ctx=STAFF_CTX)

    vendor = Vendor.objects.get(pk=current.vendor.pk)
    assert vendor.fiscal_regime == "RF01"
    assert services.current_tax_profile(vendor).regime == "domestic_only"


def test_a_fiscal_regime_change_cannot_be_approved_before_its_date() -> None:
    vendor = VendorFactory.create(fiscal_regime="RF01")
    request = submitted(
        vendor,
        regime="exempt",
        exempt_reason="flat_rate_forfettario",
        oss_registered_since=None,
        effective_from=timezone.now() + dt.timedelta(days=30),
    )
    with pytest.raises(engine.FiscalRegimeChangeNotDue):
        services.approve_regime_change(request, reviewer(), ctx=STAFF_CTX)
    vendor.refresh_from_db()
    assert vendor.fiscal_regime == "RF01"
    assert VendorTaxProfile.objects.count() == 0
    assert TaxRegimeChangeRequest.objects.get().status == engine.PENDING


def test_an_approval_that_fails_half_way_changes_nothing() -> None:
    vendor = VendorFactory.create(fiscal_regime="RF01")
    request = submitted(
        vendor,
        regime="exempt",
        exempt_reason="flat_rate_forfettario",
        oss_registered_since=None,
        effective_from=PAST,
    )
    # Staff configured a later profile directly after the request: the new version cannot
    # start before it, so the profile step fails after the fiscal regime step.
    VendorTaxProfileFactory.create(vendor=vendor, valid_from=utc(2026, 6))

    with pytest.raises(ValidationError):
        services.approve_regime_change(request, reviewer(), ctx=STAFF_CTX)
    vendor.refresh_from_db()
    assert vendor.fiscal_regime == "RF01"
    assert TaxRegimeChangeRequest.objects.get().status == engine.PENDING
    assert actions() == ["tax.regime_change.requested"]


def test_a_dry_run_approval_shows_every_change_and_writes_nothing() -> None:
    vendor = VendorFactory.create(fiscal_regime="RF01")
    request = submitted(
        vendor,
        regime="exempt",
        exempt_reason="flat_rate_forfettario",
        oss_registered_since=None,
        effective_from=PAST,
    )
    changeset = services.approve_regime_change(request, reviewer(), ctx=STAFF_CTX, dry_run=True)

    models = sorted(change.model for change in changeset.changes)
    assert models == ["tax.taxregimechangerequest", "tax.vendortaxprofile", "vendors.vendor"]
    vendor.refresh_from_db()
    assert vendor.fiscal_regime == "RF01"
    assert VendorTaxProfile.objects.count() == 0
    assert TaxRegimeChangeRequest.objects.get().status == engine.PENDING
    assert actions() == ["tax.regime_change.requested"]


def test_approval_rechecks_the_fiscal_regime_changed_since_the_request() -> None:
    vendor = VendorFactory.create(fiscal_regime="RF01")
    request = submitted(vendor)
    Vendor.objects.filter(pk=vendor.pk).update(fiscal_regime=FLAT_RATE_REGIME)

    with pytest.raises(ValidationError):
        services.approve_regime_change(request, reviewer(), ctx=STAFF_CTX)
    assert TaxRegimeChangeRequest.objects.get().status == engine.PENDING


# --- Staff rejection


def test_a_rejection_needs_a_reason_and_creates_no_profile() -> None:
    request = submitted(VendorFactory.create(fiscal_regime="RF01"))
    staff = reviewer()
    with pytest.raises(ValidationError) as exc:
        services.reject_regime_change(request, staff, reason="  ", ctx=STAFF_CTX)
    assert "review_reason" in exc.value.message_dict

    services.reject_regime_change(request, staff, reason="No OSS number.", ctx=STAFF_CTX)
    request.refresh_from_db()
    assert request.status == engine.REJECTED
    assert request.review_reason == "No OSS number."
    assert request.reviewed_by == staff
    assert VendorTaxProfile.objects.count() == 0
    assert actions() == ["tax.regime_change.requested", "tax.regime_change.rejected"]


def test_after_a_rejection_the_vendor_can_request_again() -> None:
    vendor = VendorFactory.create(fiscal_regime="RF01")
    services.reject_regime_change(submitted(vendor), reviewer(), reason="Typo.", ctx=STAFF_CTX)
    submitted(vendor)
    assert TaxRegimeChangeRequest.objects.filter(vendor=vendor).count() == 2


# --- Who may review, and when


def test_a_reviewed_request_cannot_be_reviewed_again() -> None:
    request = submitted(VendorFactory.create(fiscal_regime="RF01"))
    services.reject_regime_change(request, reviewer(), reason="No.", ctx=STAFF_CTX)
    with pytest.raises(engine.RequestNotPending):
        services.approve_regime_change(request, reviewer(), ctx=STAFF_CTX)
    with pytest.raises(engine.RequestNotPending):
        services.reject_regime_change(request, reviewer(), reason="Again.", ctx=STAFF_CTX)


def test_only_staff_other_than_the_requester_review() -> None:
    vendor = VendorFactory.create(fiscal_regime="RF01")
    requester = owner_of(vendor)
    User.objects.filter(pk=requester.pk).update(is_staff=True)
    requester.refresh_from_db()
    request = submitted(vendor, requested_by=requester)
    customer = UserFactory.create()
    for user in (requester, customer):
        with pytest.raises(engine.RegimeChangeNotAllowed):
            services.approve_regime_change(request, user, ctx=STAFF_CTX)
        with pytest.raises(engine.RegimeChangeNotAllowed):
            services.reject_regime_change(request, user, reason="No.", ctx=STAFF_CTX)
    assert TaxRegimeChangeRequest.objects.get().status == engine.PENDING


# --- History


def test_the_history_lists_requests_and_profile_versions_newest_first() -> None:
    vendor = VendorFactory.create(fiscal_regime="RF01")
    first = submitted(
        vendor, regime="domestic_only", oss_registered_since=None, effective_from=PAST
    )
    services.approve_regime_change(first, reviewer(), ctx=STAFF_CTX)
    second = submitted(vendor)
    services.reject_regime_change(second, reviewer(), reason="Not registered.", ctx=STAFF_CTX)
    third = submitted(vendor, effective_from=utc(2027, 9))

    history = services.regime_history(vendor)
    assert [r.pk for r in history.requests] == [third.pk, second.pk, first.pk]
    assert [p.regime for p in history.profiles] == ["domestic_only"]
