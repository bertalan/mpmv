"""VEN.21 (owner decision Q-17): staff reopen a closed vendor, service level.

A reopened vendor is `onboarding`, never `active`: the owner accepts the contract in force again
(an acceptance from before the closure no longer counts) and activation runs the onboarding
checklist and gates through `change_status`. The admin page is in `test_reopen_admin`.
"""

import datetime as dt
from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.vendors import engine
from apps.vendors.models import ContractAcceptance, Vendor, VendorMember
from apps.vendors.services import (
    accept_contract,
    change_status,
    contract_offer,
    onboarding_checklist,
    reopen_vendor,
    save_vendor,
)
from apps.vendors.tests.factories import (
    ContractAcceptanceFactory,
    VendorContractFactory,
    VendorFactory,
    VendorMemberFactory,
)

pytestmark = pytest.mark.django_db

STAFF_CTX = AuditContext(actor_id=1, actor_label="staff:reviewer", source="admin", ip="10.0.0.1")
REASON = "The owner asked to sell again after the winter break"


def staff_user(**kwargs: Any) -> User:
    """Platform staff who may change vendors (the admin's `change_vendor` permission)."""
    return UserFactory.create(staff=True, permissions=["vendors.change_vendor"], **kwargs)


def closed_vendor_with_owner() -> VendorMember:
    vendor = VendorFactory.create(status=Vendor.Status.CLOSED)
    return VendorMemberFactory.create(vendor=vendor)


def accept_offer(vendor: Vendor, user: User) -> None:
    offer = contract_offer("en")
    accept_contract(
        vendor,
        user,
        contract=offer.contract,
        language=offer.text.language,
        text_hash=offer.text_hash,
        ctx=AuditContext(actor_id=user.pk, actor_label="user:owner", source="vendor"),
    )


def test_staff_reopen_a_closed_vendor_to_onboarding_with_the_reason_audited() -> None:
    member = closed_vendor_with_owner()
    vendor = member.vendor

    changes = reopen_vendor(vendor, actor=staff_user(), reason=f"  {REASON} ", ctx=STAFF_CTX)

    assert changes.summary() == {"create": 0, "update": 1, "delete": 0}
    assert vendor.status == Vendor.Status.ONBOARDING
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.ONBOARDING
    assert vendor.onboarding_round == 2

    entry = AuditLog.objects.get()
    assert entry.action == "vendor.reopened"
    assert entry.target_model == "vendors.vendor"
    assert entry.target_key == str(vendor.public_id)
    assert entry.vendor_key == str(vendor.public_id)
    assert entry.before == {"status": "closed", "slug": vendor.slug, "onboarding_round": 1}
    assert entry.after == {
        "status": "onboarding",
        "slug": vendor.slug,
        "onboarding_round": 2,
        "reason": REASON,
    }
    assert (entry.actor_label, entry.source, entry.ip) == ("staff:reviewer", "admin", "10.0.0.1")


def test_a_superuser_can_reopen() -> None:
    member = closed_vendor_with_owner()
    reopen_vendor(
        member.vendor, actor=UserFactory.create(superuser=True), reason=REASON, ctx=STAFF_CTX
    )
    member.vendor.refresh_from_db()
    assert member.vendor.status == Vendor.Status.ONBOARDING


@pytest.mark.parametrize("reason", ["", "   "])
def test_a_reopen_without_a_reason_is_refused(reason: str) -> None:
    member = closed_vendor_with_owner()
    for dry_run in (True, False):
        with pytest.raises(engine.ReasonRequired):
            reopen_vendor(
                member.vendor, actor=staff_user(), reason=reason, ctx=STAFF_CTX, dry_run=dry_run
            )
    member.vendor.refresh_from_db()
    assert member.vendor.status == Vendor.Status.CLOSED
    assert member.vendor.onboarding_round == 1
    assert not AuditLog.objects.exists()


def test_vendor_members_cannot_reopen_their_vendor() -> None:
    member = closed_vendor_with_owner()
    colleague = VendorMemberFactory.create(vendor=member.vendor, role=VendorMember.Role.STAFF)
    for user in (member.user, colleague.user):
        with pytest.raises(PermissionDenied):
            reopen_vendor(member.vendor, actor=user, reason=REASON, ctx=STAFF_CTX)
    member.vendor.refresh_from_db()
    assert member.vendor.status == Vendor.Status.CLOSED
    assert not AuditLog.objects.exists()


@pytest.mark.parametrize(
    "make_actor",
    [
        pytest.param(lambda: UserFactory.create(staff=True), id="staff-without-change-permission"),
        pytest.param(lambda: staff_user(is_active=False), id="inactive-staff"),
        pytest.param(
            lambda: UserFactory.create(permissions=["vendors.change_vendor"]),
            id="permission-without-staff",
        ),
        pytest.param(AnonymousUser, id="anonymous"),
    ],
)
def test_only_active_staff_with_the_change_permission_can_reopen(make_actor: Any) -> None:
    member = closed_vendor_with_owner()
    with pytest.raises(PermissionDenied):
        reopen_vendor(member.vendor, actor=make_actor(), reason=REASON, ctx=STAFF_CTX, dry_run=True)
    member.vendor.refresh_from_db()
    assert member.vendor.status == Vendor.Status.CLOSED


@pytest.mark.parametrize("status", ["applicant", "onboarding", "active", "suspended"])
def test_only_a_closed_vendor_can_be_reopened(status: str) -> None:
    vendor = VendorFactory.create(status=status)
    VendorMemberFactory.create(vendor=vendor)
    with pytest.raises(engine.InvalidTransition, match=f"{status} → onboarding|reason"):
        reopen_vendor(vendor, actor=staff_user(), reason=REASON, ctx=STAFF_CTX)
    vendor.refresh_from_db()
    assert vendor.status == status
    assert not AuditLog.objects.exists()


def test_closed_to_active_stays_refused() -> None:
    member = closed_vendor_with_owner()
    ContractAcceptanceFactory.create(vendor=member.vendor, user=member.user)
    with pytest.raises(engine.InvalidTransition, match="closed → active"):
        change_status(member.vendor, Vendor.Status.ACTIVE, ctx=STAFF_CTX)
    member.vendor.refresh_from_db()
    assert member.vendor.status == Vendor.Status.CLOSED


def test_the_generic_status_change_cannot_reopen_without_a_reason() -> None:
    member = closed_vendor_with_owner()
    with pytest.raises(engine.ReasonRequired):
        change_status(member.vendor, Vendor.Status.ONBOARDING, ctx=STAFF_CTX)
    member.vendor.refresh_from_db()
    assert member.vendor.status == Vendor.Status.CLOSED
    assert not AuditLog.objects.exists()


def test_a_reopen_dry_run_returns_the_change_and_applies_nothing() -> None:
    member = closed_vendor_with_owner()
    changes = reopen_vendor(
        member.vendor, actor=staff_user(), reason=REASON, ctx=STAFF_CTX, dry_run=True
    )
    [change] = changes.changes
    assert change.after is not None
    assert change.after["status"] == "onboarding"
    assert change.after["reason"] == REASON
    assert member.vendor.status == Vendor.Status.CLOSED
    member.vendor.refresh_from_db()
    assert member.vendor.status == Vendor.Status.CLOSED
    assert member.vendor.onboarding_round == 1
    assert not AuditLog.objects.exists()


def test_a_reopened_vendor_must_accept_the_contract_in_force_again() -> None:
    contract = VendorContractFactory.create()
    vendor = VendorFactory.create(status=Vendor.Status.ONBOARDING)
    acceptance = ContractAcceptanceFactory.create(vendor=vendor, contract=contract)
    owner = acceptance.user
    change_status(vendor, Vendor.Status.ACTIVE, ctx=STAFF_CTX)
    change_status(vendor, Vendor.Status.CLOSED, ctx=STAFF_CTX)

    reopen_vendor(vendor, actor=staff_user(), reason=REASON, ctx=STAFF_CTX)

    checklist = onboarding_checklist(vendor)
    assert checklist.blocking_missing == (engine.CONTRACT,)
    with pytest.raises(engine.ActivationNotAllowed, match="contract"):
        change_status(vendor, Vendor.Status.ACTIVE, ctx=STAFF_CTX)

    accept_offer(vendor, owner)  # the same version, accepted again in the new round
    assert ContractAcceptance.objects.filter(vendor=vendor, contract=contract).count() == 2
    assert onboarding_checklist(vendor).ready
    change_status(vendor, Vendor.Status.ACTIVE, ctx=STAFF_CTX)
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.ACTIVE


def test_a_reopened_vendor_accepts_a_version_once_per_round() -> None:
    VendorContractFactory.create()
    member = closed_vendor_with_owner()
    reopen_vendor(member.vendor, actor=staff_user(), reason=REASON, ctx=STAFF_CTX)
    accept_offer(member.vendor, member.user)
    accept_offer(member.vendor, member.user)
    acceptance = ContractAcceptance.objects.get(vendor=member.vendor)
    assert acceptance.onboarding_round == 2


def test_a_newer_contract_version_is_the_one_a_reopened_vendor_accepts() -> None:
    old = VendorContractFactory.create(valid_from=timezone.now() - dt.timedelta(days=60))
    acceptance = ContractAcceptanceFactory.create(
        contract=old, vendor=VendorFactory.create(status=Vendor.Status.CLOSED)
    )
    newer = VendorContractFactory.create()  # published while the vendor was closed
    reopen_vendor(acceptance.vendor, actor=staff_user(), reason=REASON, ctx=STAFF_CTX)

    checklist = onboarding_checklist(acceptance.vendor)
    assert checklist.blocking_missing == (engine.CONTRACT,)
    assert checklist.current_contract_version == newer.version
    accept_offer(acceptance.vendor, acceptance.user)
    latest = ContractAcceptance.objects.filter(vendor=acceptance.vendor).first()  # newest first
    assert latest is not None
    assert (latest.contract, latest.onboarding_round) == (newer, 2)
    assert onboarding_checklist(acceptance.vendor).ready


def test_a_vendor_whose_owners_are_all_inactive_is_reopened_only_with_a_new_owner() -> None:
    member = closed_vendor_with_owner()
    member.user.is_active = False  # e.g. an erasure request restricts the account (Q-16)
    member.user.save(update_fields=["is_active"])
    with pytest.raises(engine.ReopenNotAllowed):
        reopen_vendor(member.vendor, actor=staff_user(), reason=REASON, ctx=STAFF_CTX)
    member.vendor.refresh_from_db()
    assert member.vendor.status == Vendor.Status.CLOSED

    VendorMemberFactory.create(vendor=member.vendor)  # a new owner takes over
    reopen_vendor(member.vendor, actor=staff_user(), reason=REASON, ctx=STAFF_CTX)
    member.vendor.refresh_from_db()
    assert member.vendor.status == Vendor.Status.ONBOARDING


def test_the_reopen_checks_the_stored_status_not_a_stale_instance() -> None:
    member = closed_vendor_with_owner()
    stale = Vendor.objects.get(pk=member.vendor.pk)
    reopen_vendor(member.vendor, actor=staff_user(), reason=REASON, ctx=STAFF_CTX)
    with pytest.raises(engine.InvalidTransition):
        reopen_vendor(stale, actor=staff_user(), reason=REASON, ctx=STAFF_CTX)
    member.vendor.refresh_from_db()
    assert member.vendor.onboarding_round == 2


def test_saving_a_stale_profile_does_not_undo_a_reopen() -> None:
    member = closed_vendor_with_owner()
    stale = Vendor.objects.get(pk=member.vendor.pk)  # e.g. an admin form opened before
    reopen_vendor(member.vendor, actor=staff_user(), reason=REASON, ctx=STAFF_CTX)

    stale.phone = "+39 06 000000"
    save_vendor(stale, ctx=STAFF_CTX)

    stored = Vendor.objects.get(pk=member.vendor.pk)
    assert stored.phone == "+39 06 000000"
    assert (stored.status, stored.onboarding_round) == (Vendor.Status.ONBOARDING, 2)
