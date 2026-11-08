"""VEN.24 (owner decision MQ-1): claim a vendor imported without an account holder.

Staff send a single-use, expiring CORE.15 link to the vendor's legal email of record. The
claimant confirms the stored VAT number or TIN, sets a password and enrols an authenticator
app; the claim then waits for staff approval, and only approval makes the claimant an owner who
can change fiscal or payout data. Regenerating the link invalidates the old one.
"""

import json
import secrets
import time
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django_otp.oath import TOTP
from django_otp.plugins.otp_totp.models import TOTPDevice
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.accounts import engine as accounts_engine
from apps.accounts import services as accounts_services
from apps.accounts.models import ActionLinkUse, User
from apps.accounts.tests.factories import UserFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog, OutboxEmail
from apps.vendors import engine, services, views
from apps.vendors.models import Vendor, VendorClaim, VendorMember
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db

SYSTEM = AuditContext(actor_label="system:test", source=AuditLog.Source.SYSTEM)
FISCAL_ACTIONS = (
    engine.VendorAction.BANK_ACCOUNT,
    engine.VendorAction.TAX_REGIME,
    engine.VendorAction.PAYOUTS,
    engine.VendorAction.PROFILE,
    engine.VendorAction.MEMBERS,
)


def imported_vendor(**kwargs: Any) -> Vendor:
    return VendorFactory.create(
        italian_company=True, contact_email="legal@vendor-a.example", **kwargs
    )


def link_path(email: OutboxEmail) -> str:
    url = str(json.loads(email.context)["action_url"])
    return "/" + url.split("/", 3)[3]


def send_link(vendor: Vendor) -> str:
    """Send (or regenerate) the claim link and return the path of the link in the email."""
    services.send_claim_link(vendor, ctx=SYSTEM)
    return link_path(OutboxEmail.objects.order_by("-id").first())  # type: ignore[arg-type]


def token_of(path: str) -> str:
    return path.rstrip("/").rsplit("/", 1)[-1]


def open_link(client: Client, path: str) -> Any:
    """What the claimant does: open the emailed link, then press Continue."""
    client.get(path)
    return client.post(reverse("action_link:continue"), {"token": token_of(path)})


def claim_url(vendor: Vendor) -> str:
    return reverse("vendor_claim:claim", args=[vendor.public_id])


def confirm_identity(client: Client, vendor: Vendor, tax_id: str) -> Any:
    return client.post(claim_url(vendor), {"step": "identity", "tax_id": tax_id})


def pending_totp_code(client: Client, vendor: Vendor) -> str:
    key = client.session[views.CLAIM_SESSION_KEY][str(vendor.public_id)]["totp_key"]
    totp = TOTP(bytes.fromhex(key), 30, 0, 6, 0)
    return str(totp.token()).zfill(6)


def create_account(client: Client, vendor: Vendor, *, code: str | None = None) -> Any:
    password = secrets.token_urlsafe(16)
    return client.post(
        claim_url(vendor),
        {
            "step": "account",
            "full_name": "Claimant Example",
            "password1": password,
            "password2": password,
            "code": code if code is not None else pending_totp_code(client, vendor),
        },
    )


def full_claim(client: Client, vendor: Vendor) -> Any:
    open_link(client, send_link(vendor))
    confirm_identity(client, vendor, vendor.vat_number)
    return create_account(client, vendor)


def actions(name: str) -> list[AuditLog]:
    return list(AuditLog.objects.filter(action=name).order_by("id"))


# --- Sending the link


def test_the_link_goes_only_to_the_legal_email_of_record() -> None:
    vendor = imported_vendor()
    services.send_claim_link(vendor, ctx=SYSTEM)
    email = OutboxEmail.objects.get()
    assert email.recipient == "legal@vendor-a.example"
    assert email.template_key == services.CLAIM_EMAIL_TEMPLATE
    claim = VendorClaim.objects.get(vendor=vendor)
    assert claim.status == VendorClaim.Status.OPEN
    assert claim.version == 1
    (sent,) = actions(services.CLAIM_LINK_SENT)
    assert sent.vendor_key == str(vendor.public_id)
    assert token_of(link_path(email)) not in json.dumps([sent.before, sent.after])


def test_sending_takes_no_address_from_the_caller() -> None:
    with pytest.raises(TypeError):
        services.send_claim_link(  # type: ignore[call-arg]
            imported_vendor(), ctx=SYSTEM, email="attacker@example.com"
        )


def test_a_dry_run_sends_nothing() -> None:
    vendor = imported_vendor()
    changes = services.send_claim_link(vendor, ctx=SYSTEM, dry_run=True)
    assert not changes.is_empty
    assert not OutboxEmail.objects.exists()
    assert not VendorClaim.objects.exists()
    assert not AuditLog.objects.exists()


def test_a_vendor_with_an_account_holder_cannot_be_claimed() -> None:
    vendor = imported_vendor()
    VendorMemberFactory.create(vendor=vendor)
    with pytest.raises(engine.ClaimError):
        services.send_claim_link(vendor, ctx=SYSTEM)
    assert not OutboxEmail.objects.exists()


def test_a_vendor_without_a_vat_number_or_tin_cannot_be_claimed() -> None:
    vendor = VendorFactory.create(vat_number="", tax_code="")
    with pytest.raises(engine.ClaimError):
        services.send_claim_link(vendor, ctx=SYSTEM)


def test_a_vendor_without_a_legal_email_cannot_be_claimed() -> None:
    vendor = imported_vendor()
    Vendor.objects.filter(pk=vendor.pk).update(contact_email="")
    vendor.refresh_from_db()
    with pytest.raises(engine.ClaimError):
        services.send_claim_link(vendor, ctx=SYSTEM)


# --- Opening the link


@pytest.mark.parametrize("method", ["get", "head"])
def test_a_get_does_not_consume_the_link(client: Client, method: str) -> None:
    vendor = imported_vendor()
    path = send_link(vendor)
    getattr(Client(), method)(path)  # a mail scanner
    assert not ActionLinkUse.objects.exists()
    assert VendorClaim.objects.get(vendor=vendor).status == VendorClaim.Status.OPEN
    response = open_link(client, path)
    assert response["Location"] == claim_url(vendor)
    page = client.get(claim_url(vendor))
    assert page.status_code == 200
    assert 'name="tax_id"' in page.content.decode()


def test_the_claim_page_without_the_link_is_refused(client: Client) -> None:
    vendor = imported_vendor()
    send_link(vendor)
    response = client.get(claim_url(vendor))
    assert response.status_code == 403
    assert vendor.legal_name not in response.content.decode()


def test_a_signed_in_user_cannot_claim(client: Client) -> None:
    vendor = imported_vendor()
    path = send_link(vendor)
    client.force_login(UserFactory.create())
    open_link(client, path)
    response = confirm_identity(client, vendor, vendor.vat_number)
    assert response.status_code == 403
    assert VendorClaim.objects.get(vendor=vendor).confirmed_link == ""


def test_an_expired_link_is_refused(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    vendor = imported_vendor()
    path = send_link(vendor)
    later = time.time() + 30 * 24 * 3600

    class Later:
        @staticmethod
        def time() -> float:
            return later

    monkeypatch.setattr(accounts_services, "time", Later)
    open_link(client, path)
    response = client.get(claim_url(vendor))
    assert response.status_code == 403
    refused = actions(accounts_services.ACTION_LINK_REFUSED)
    assert [(entry.after or {})["reason"] for entry in refused] == [accounts_engine.EXPIRED]


def test_a_new_legal_email_invalidates_the_link(client: Client) -> None:
    vendor = imported_vendor()
    path = send_link(vendor)
    Vendor.objects.filter(pk=vendor.pk).update(contact_email="other@vendor-a.example")
    open_link(client, path)
    assert client.get(claim_url(vendor)).status_code == 403


# --- Confirming the VAT number or TIN


@pytest.mark.parametrize("field", ["vat_number", "tax_code"])
def test_the_stored_vat_number_or_tin_confirms_the_identity(client: Client, field: str) -> None:
    vendor = imported_vendor(tax_code="RSSMRA80A01H501U")
    open_link(client, send_link(vendor))
    response = confirm_identity(client, vendor, getattr(vendor, field))
    assert response.status_code == 200
    assert 'name="password1"' in response.content.decode()
    claim = VendorClaim.objects.get(vendor=vendor)
    assert claim.confirmed_link != ""
    assert claim.failed_attempts == 0


def test_a_wrong_vat_number_is_refused_and_audited(client: Client) -> None:
    vendor = imported_vendor()
    open_link(client, send_link(vendor))
    wrong = "IT00000000000"
    response = confirm_identity(client, vendor, wrong)
    assert response.status_code == 200
    assert 'name="password1"' not in response.content.decode()
    assert "does not match" in response.content.decode()
    claim = VendorClaim.objects.get(vendor=vendor)
    assert claim.failed_attempts == 1
    assert claim.confirmed_link == ""
    (refused,) = actions(services.CLAIM_IDENTITY_REFUSED)
    assert refused.vendor_key == str(vendor.public_id)
    assert (refused.after or {})["failed_attempts"] == 1
    assert wrong not in json.dumps([refused.before, refused.after])
    assert vendor.vat_number not in json.dumps([refused.before, refused.after])


def test_the_claim_locks_after_too_many_wrong_answers(
    client: Client, settings: SettingsWrapper
) -> None:
    settings.VENDOR_CLAIM_MAX_FAILURES = 3
    vendor = imported_vendor()
    open_link(client, send_link(vendor))
    for _ in range(3):
        confirm_identity(client, vendor, "IT00000000000")
    claim = VendorClaim.objects.get(vendor=vendor)
    assert claim.status == VendorClaim.Status.LOCKED
    assert len(actions(services.CLAIM_IDENTITY_REFUSED)) == 3
    assert len(actions(services.CLAIM_LOCKED)) == 1
    # Even the right answer is refused now, and the link with it.
    response = confirm_identity(client, vendor, vendor.vat_number)
    assert response.status_code == 403
    assert VendorClaim.objects.get(vendor=vendor).confirmed_link == ""


def test_claim_posts_are_rate_limited_per_ip(client: Client, settings: SettingsWrapper) -> None:
    settings.VENDOR_CLAIM_RATE_LIMIT = 2
    vendor = imported_vendor()
    open_link(client, send_link(vendor))
    confirm_identity(client, vendor, "IT00000000000")
    confirm_identity(client, vendor, "IT00000000000")
    response = confirm_identity(client, vendor, vendor.vat_number)
    assert response.status_code == 429
    assert "Retry-After" in response
    assert VendorClaim.objects.get(vendor=vendor).failed_attempts == 2


# --- Creating the account and submitting the claim


def test_the_account_step_needs_the_identity_first(client: Client) -> None:
    vendor = imported_vendor()
    open_link(client, send_link(vendor))
    response = create_account(client, vendor, code="123456")
    assert response.status_code == 403
    assert not User.objects.filter(email=vendor.contact_email).exists()


def test_the_claim_is_submitted_with_a_password_and_an_authenticator(client: Client) -> None:
    vendor = imported_vendor()
    response = full_claim(client, vendor)
    assert response.status_code == 200
    body = response.content.decode()
    assert "waiting for approval" in body
    user = User.objects.get(email=vendor.contact_email)
    assert user.has_usable_password()
    assert TOTPDevice.objects.filter(user=user, confirmed=True).exists()
    assert accounts_services.unused_recovery_codes(user) > 0
    claim = VendorClaim.objects.get(vendor=vendor)
    assert claim.status == VendorClaim.Status.SUBMITTED
    assert claim.claimant == user
    assert ActionLinkUse.objects.get().user == user
    assert int(client.session["_auth_user_id"]) == user.pk
    (submitted,) = actions(services.CLAIM_SUBMITTED)
    assert submitted.vendor_key == str(vendor.public_id)
    assert (submitted.after or {})["claimant"] == str(user.public_id)


def test_a_wrong_authenticator_code_creates_nothing(client: Client) -> None:
    vendor = imported_vendor()
    open_link(client, send_link(vendor))
    confirm_identity(client, vendor, vendor.vat_number)
    good = pending_totp_code(client, vendor)
    response = create_account(client, vendor, code=str((int(good) + 1) % 1_000_000).zfill(6))
    assert response.status_code == 200
    assert "Invalid code" in response.content.decode()
    assert not User.objects.filter(email=vendor.contact_email).exists()
    assert VendorClaim.objects.get(vendor=vendor).status == VendorClaim.Status.OPEN


def test_a_weak_password_is_refused(client: Client) -> None:
    vendor = imported_vendor()
    open_link(client, send_link(vendor))
    confirm_identity(client, vendor, vendor.vat_number)
    response = client.post(
        claim_url(vendor),
        {
            "step": "account",
            "full_name": "Claimant Example",
            "password1": "short",
            "password2": "short",
            "code": pending_totp_code(client, vendor),
        },
    )
    assert response.status_code == 200
    assert not User.objects.filter(email=vendor.contact_email).exists()


def test_an_existing_account_with_the_legal_email_is_not_taken_over(client: Client) -> None:
    vendor = imported_vendor()
    existing = UserFactory.create(email=vendor.contact_email)
    open_link(client, send_link(vendor))
    confirm_identity(client, vendor, vendor.vat_number)
    response = create_account(client, vendor)
    assert response.status_code == 200
    assert "already" in response.content.decode()
    existing.refresh_from_db()
    assert not TOTPDevice.objects.filter(user=existing).exists()
    assert VendorClaim.objects.get(vendor=vendor).status == VendorClaim.Status.OPEN


def test_a_submitted_link_cannot_be_used_again(client: Client) -> None:
    vendor = imported_vendor()
    path = send_link(vendor)
    open_link(client, path)
    confirm_identity(client, vendor, vendor.vat_number)
    create_account(client, vendor)
    other = Client()
    open_link(other, path)
    assert other.get(claim_url(vendor)).status_code == 403


# --- Staff approval


def test_before_approval_the_claimant_cannot_change_fiscal_or_payout_data(
    client: Client,
) -> None:
    vendor = imported_vendor()
    full_claim(client, vendor)
    claimant = User.objects.get(email=vendor.contact_email)
    assert not VendorMember.objects.filter(vendor=vendor).exists()
    for action in FISCAL_ACTIONS:
        assert not services.has_vendor_permission(claimant, vendor, action)


def test_staff_approval_makes_the_claimant_the_owner(client: Client) -> None:
    vendor = imported_vendor()
    full_claim(client, vendor)
    claim = VendorClaim.objects.get(vendor=vendor)
    staff = AuditContext(actor_label="staff:test", source=AuditLog.Source.ADMIN)
    services.approve_claim(claim, ctx=staff)
    claim.refresh_from_db()
    assert claim.status == VendorClaim.Status.APPROVED
    claimant = User.objects.get(email=vendor.contact_email)
    assert services.member_role(claimant, vendor) == engine.OWNER
    for action in FISCAL_ACTIONS:
        assert services.has_vendor_permission(claimant, vendor, action)
    (approved,) = actions(services.CLAIM_APPROVED)
    assert approved.vendor_key == str(vendor.public_id)


def test_approval_needs_a_submitted_claim() -> None:
    vendor = imported_vendor()
    send_link(vendor)
    with pytest.raises(engine.ClaimError):
        services.approve_claim(VendorClaim.objects.get(vendor=vendor), ctx=SYSTEM)


def test_approval_dry_run_changes_nothing(client: Client) -> None:
    vendor = imported_vendor()
    full_claim(client, vendor)
    claim = VendorClaim.objects.get(vendor=vendor)
    changes = services.approve_claim(claim, ctx=SYSTEM, dry_run=True)
    assert not changes.is_empty
    assert VendorClaim.objects.get(vendor=vendor).status == VendorClaim.Status.SUBMITTED
    assert not VendorMember.objects.filter(vendor=vendor).exists()


def test_a_rejected_claim_grants_nothing_and_can_be_sent_again(client: Client) -> None:
    vendor = imported_vendor()
    full_claim(client, vendor)
    claim = VendorClaim.objects.get(vendor=vendor)
    services.reject_claim(claim, ctx=SYSTEM)
    claim.refresh_from_db()
    assert claim.status == VendorClaim.Status.REJECTED
    assert not VendorMember.objects.filter(vendor=vendor).exists()
    assert len(actions(services.CLAIM_REJECTED)) == 1
    services.send_claim_link(vendor, ctx=SYSTEM)
    assert VendorClaim.objects.get(vendor=vendor).status == VendorClaim.Status.OPEN


# --- Resend / regenerate


def test_regenerating_refuses_the_old_link_and_audits_both_steps(client: Client) -> None:
    vendor = imported_vendor()
    old = send_link(vendor)
    new = send_link(vendor)
    assert old != new
    claim = VendorClaim.objects.get(vendor=vendor)
    assert claim.version == 2
    (regenerated,) = actions(services.CLAIM_LINK_REGENERATED)
    (invalidated,) = actions(services.CLAIM_LINK_INVALIDATED)
    assert (regenerated.before or {})["version"] == 1
    assert (regenerated.after or {})["version"] == 2
    assert (invalidated.before or {})["version"] == 1
    assert invalidated.vendor_key == str(vendor.public_id)
    assert OutboxEmail.objects.filter(recipient=vendor.contact_email).count() == 2

    open_link(client, old)
    assert client.get(claim_url(vendor)).status_code == 403
    refused = actions(accounts_services.ACTION_LINK_REFUSED)
    assert [(entry.after or {})["reason"] for entry in refused] == [accounts_engine.OLD_VERSION]

    fresh = Client()
    open_link(fresh, new)
    assert fresh.get(claim_url(vendor)).status_code == 200


def test_regenerating_unlocks_a_locked_claim(client: Client, settings: SettingsWrapper) -> None:
    settings.VENDOR_CLAIM_MAX_FAILURES = 1
    vendor = imported_vendor()
    open_link(client, send_link(vendor))
    confirm_identity(client, vendor, "IT00000000000")
    assert VendorClaim.objects.get(vendor=vendor).status == VendorClaim.Status.LOCKED
    fresh = Client()
    open_link(fresh, send_link(vendor))
    claim = VendorClaim.objects.get(vendor=vendor)
    assert claim.status == VendorClaim.Status.OPEN
    assert claim.failed_attempts == 0
    assert confirm_identity(fresh, vendor, vendor.vat_number).status_code == 200


def test_a_regenerated_link_drops_a_confirmed_identity(client: Client) -> None:
    vendor = imported_vendor()
    open_link(client, send_link(vendor))
    confirm_identity(client, vendor, vendor.vat_number)
    send_link(vendor)
    assert create_account(client, vendor).status_code == 403
    assert not User.objects.filter(email=vendor.contact_email).exists()


def test_a_pending_claim_cannot_be_regenerated(client: Client) -> None:
    vendor = imported_vendor()
    full_claim(client, vendor)
    with pytest.raises(engine.ClaimError):
        services.send_claim_link(vendor, ctx=SYSTEM)
