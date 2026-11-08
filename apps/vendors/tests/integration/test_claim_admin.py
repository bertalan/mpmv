"""VEN.24: staff send, resend / regenerate, approve and reject vendor claims from the admin."""

from collections.abc import Callable
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog, OutboxEmail
from apps.vendors import engine, services
from apps.vendors.models import Vendor, VendorClaim, VendorMember
from apps.vendors.tests.factories import (
    VendorClaimFactory,
    VendorFactory,
    VendorMemberFactory,
)

pytestmark = pytest.mark.django_db

MfaLogin = Callable[[Client, User], Client]
SYSTEM = AuditContext(actor_label="system:test", source=AuditLog.Source.SYSTEM)
CHANGELIST = "admin:vendors_vendor_changelist"


@pytest.fixture
def staff_client(client: Client, superuser: User, mfa_login: MfaLogin) -> Client:
    return mfa_login(client, superuser)


def run_action(client: Client, action: str, vendor: Vendor) -> Any:
    response = client.post(
        reverse(CHANGELIST), {"action": action, "_selected_action": [vendor.pk]}, follow=True
    )
    assert response.status_code == 200
    return response


def messages_of(response: Any) -> list[str]:
    return [str(message) for message in response.context["messages"]]


def submitted_claim(vendor: Vendor) -> VendorClaim:
    """A claim in the state the claim page leaves it: submitted by a new account."""
    services.send_claim_link(vendor, ctx=SYSTEM)
    claimant = UserFactory.create(email=vendor.contact_email)
    VendorClaim.objects.filter(vendor=vendor).update(
        status=VendorClaim.Status.SUBMITTED, claimant=claimant, version=2
    )
    return VendorClaim.objects.get(vendor=vendor)


def test_staff_send_the_claim_link(staff_client: Client) -> None:
    vendor = VendorFactory.create(italian_company=True)
    response = run_action(staff_client, "send_claim_link", vendor)
    assert any("sent" in text for text in messages_of(response))
    assert OutboxEmail.objects.get().recipient == vendor.contact_email
    assert VendorClaim.objects.get(vendor=vendor).status == VendorClaim.Status.OPEN


def test_staff_regenerate_the_claim_link(staff_client: Client) -> None:
    vendor = VendorFactory.create(italian_company=True)
    run_action(staff_client, "send_claim_link", vendor)
    run_action(staff_client, "send_claim_link", vendor)
    assert VendorClaim.objects.get(vendor=vendor).version == 2
    assert AuditLog.objects.filter(action=services.CLAIM_LINK_INVALIDATED).count() == 1
    entry = AuditLog.objects.get(action=services.CLAIM_LINK_REGENERATED)
    assert entry.actor_label != SYSTEM.actor_label


def test_a_vendor_with_an_owner_gets_an_error_not_a_link(staff_client: Client) -> None:
    vendor = VendorFactory.create(italian_company=True)
    VendorMemberFactory.create(vendor=vendor)
    response = run_action(staff_client, "send_claim_link", vendor)
    assert any("account holder" in text for text in messages_of(response))
    assert not OutboxEmail.objects.exists()


def test_staff_approve_a_submitted_claim(staff_client: Client) -> None:
    vendor = VendorFactory.create(italian_company=True)
    claim = submitted_claim(vendor)
    run_action(staff_client, "approve_claim", vendor)
    claim.refresh_from_db()
    assert claim.status == VendorClaim.Status.APPROVED
    assert VendorMember.objects.get(vendor=vendor).role == engine.OWNER


def test_staff_reject_a_submitted_claim(staff_client: Client) -> None:
    vendor = VendorFactory.create(italian_company=True)
    claim = submitted_claim(vendor)
    run_action(staff_client, "reject_claim", vendor)
    claim.refresh_from_db()
    assert claim.status == VendorClaim.Status.REJECTED
    assert not VendorMember.objects.filter(vendor=vendor).exists()


def test_approving_without_a_submitted_claim_is_an_error(staff_client: Client) -> None:
    vendor = VendorFactory.create(italian_company=True)
    response = run_action(staff_client, "approve_claim", vendor)
    assert any("no claim" in text.lower() for text in messages_of(response))


def test_the_change_page_shows_the_claim_status(staff_client: Client) -> None:
    vendor = VendorFactory.create(italian_company=True)
    submitted_claim(vendor)
    page = staff_client.get(reverse("admin:vendors_vendor_change", args=[vendor.pk]))
    assert "Submitted, waiting for approval" in page.content.decode()

    locked = VendorClaimFactory.create(status=VendorClaim.Status.LOCKED, version=3)
    page = staff_client.get(reverse("admin:vendors_vendor_change", args=[locked.vendor.pk]))
    assert "Locked after too many wrong answers (link version 3)" in page.content.decode()
