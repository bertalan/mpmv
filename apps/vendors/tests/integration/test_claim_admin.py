"""VEN.24: staff send, resend / regenerate, approve and reject vendor claims from the admin."""

from collections.abc import Callable
from typing import Any, cast
from urllib.parse import urlsplit

import pytest
from django.contrib import admin
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import Client, RequestFactory
from django.urls import reverse
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.core.audit import AuditContext
from apps.core.models import AuditLog, OutboxEmail
from apps.vendors import engine, services
from apps.vendors.admin import VendorAdmin
from apps.vendors.models import Vendor, VendorClaim, VendorMember
from apps.vendors.tests.factories import (
    VendorClaimFactory,
    VendorFactory,
    VendorMemberFactory,
)

pytestmark = pytest.mark.django_db

MfaLogin = Callable[[Client, User], Client]
SYSTEM = AuditContext(actor_label="system:test", source=AuditLog.Source.SYSTEM)
STAFF_B = AuditContext(actor_label="staff:b", source=AuditLog.Source.ADMIN)
CHANGELIST = "admin:vendors_vendor_changelist"


@pytest.fixture
def staff_client(client: Client, superuser: User) -> Client:
    # Approving a claim grants the owner role: D-32 critical, so the session has a fresh passkey.
    verified_login(client, superuser, passkey=True)
    return client


def run_action(client: Client, action: str, vendor: Vendor) -> Any:
    response = client.post(
        reverse(CHANGELIST), {"action": action, "_selected_action": [vendor.pk]}, follow=True
    )
    assert response.status_code == 200
    return response


def reviewed(claim: VendorClaim) -> dict[str, str]:
    """What the review page posts back: the link version and the claimant staff saw."""
    return {f"reviewed-{claim.vendor_id}": f"{claim.version}:{claim.claimant_id}"}


def decide(client: Client, action: str, vendor: Vendor) -> Any:
    """Approve or reject as staff do: the action shows the claim for review, then the
    confirmation posts back what was reviewed."""
    selected = {"action": action, "_selected_action": [vendor.pk]}
    page = client.post(reverse(CHANGELIST), selected)
    assert page.status_code == 200
    data = {**selected, "post": "yes", **page.context["reviewed_fields"]}
    response = client.post(reverse(CHANGELIST), data, follow=True)
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
    decide(staff_client, "approve_claim", vendor)
    claim.refresh_from_db()
    assert claim.status == VendorClaim.Status.APPROVED
    assert VendorMember.objects.get(vendor=vendor).role == engine.OWNER


def test_staff_reject_a_submitted_claim(staff_client: Client) -> None:
    vendor = VendorFactory.create(italian_company=True)
    claim = submitted_claim(vendor)
    decide(staff_client, "reject_claim", vendor)
    claim.refresh_from_db()
    assert claim.status == VendorClaim.Status.REJECTED
    assert not VendorMember.objects.filter(vendor=vendor).exists()


def test_approving_without_a_submitted_claim_is_an_error(staff_client: Client) -> None:
    vendor = VendorFactory.create(italian_company=True)
    response = staff_client.post(
        reverse(CHANGELIST), {"action": "approve_claim", "_selected_action": [vendor.pk]}
    )
    assert "no claim" in response.content.decode().lower()
    assert not VendorMember.objects.filter(vendor=vendor).exists()


@pytest.mark.parametrize("action", ["approve_claim", "reject_claim"])
def test_deciding_shows_the_claim_for_review_first(staff_client: Client, action: str) -> None:
    vendor = VendorFactory.create(italian_company=True)
    claim = submitted_claim(vendor)
    page = staff_client.post(
        reverse(CHANGELIST), {"action": action, "_selected_action": [vendor.pk]}
    )
    assert page.status_code == 200
    html = page.content.decode()
    assert claim.claimant is not None
    assert claim.claimant.email in html
    assert f"link version {claim.version}" in html
    assert page.context["reviewed_fields"] == reviewed(claim)
    claim.refresh_from_db()
    assert claim.status == VendorClaim.Status.SUBMITTED
    assert not VendorMember.objects.filter(vendor=vendor).exists()


@pytest.mark.parametrize("action", ["approve_claim", "reject_claim"])
def test_a_decision_without_the_reviewed_claim_is_refused(
    staff_client: Client, action: str
) -> None:
    vendor = VendorFactory.create(italian_company=True)
    claim = submitted_claim(vendor)
    response = staff_client.post(
        reverse(CHANGELIST),
        {"action": action, "_selected_action": [vendor.pk], "post": "yes"},
        follow=True,
    )
    assert any("review" in text for text in messages_of(response))
    claim.refresh_from_db()
    assert claim.status == VendorClaim.Status.SUBMITTED
    assert not VendorMember.objects.filter(vendor=vendor).exists()


@pytest.mark.parametrize("value", ["\u00b2:1", "1:\u2460", "1" * 5000 + ":1", "9" * 30 + ":1"])
def test_a_hostile_reviewed_claim_value_is_refused_without_an_error(
    staff_client: Client, value: str
) -> None:
    vendor = VendorFactory.create(italian_company=True)
    claim = submitted_claim(vendor)
    response = staff_client.post(
        reverse(CHANGELIST),
        {
            "action": "approve_claim",
            "_selected_action": [vendor.pk],
            "post": "yes",
            f"reviewed-{vendor.pk}": value,
        },
        follow=True,
    )
    assert response.status_code == 200
    claim.refresh_from_db()
    assert claim.status == VendorClaim.Status.SUBMITTED


def test_staff_a_cannot_approve_a_claimant_they_never_saw(staff_client: Client) -> None:
    """Staff A opens the review; meanwhile staff B rejects, resends the link to the PEC and
    the PEC holder submits. A's confirmation must not approve the new claimant."""
    vendor = VendorFactory.create(italian_company=True)
    claim = submitted_claim(vendor)
    seen_by_a = reviewed(claim)
    services.reject_claim(claim, ctx=STAFF_B)
    services.send_claim_link(vendor, ctx=STAFF_B, channel=engine.CLAIM_CHANNEL_PEC)
    newcomer = UserFactory.create(email=vendor.pec)
    VendorClaim.objects.filter(vendor=vendor).update(
        status=VendorClaim.Status.SUBMITTED, claimant=newcomer
    )
    response = staff_client.post(
        reverse(CHANGELIST),
        {"action": "approve_claim", "_selected_action": [vendor.pk], "post": "yes", **seen_by_a},
        follow=True,
    )
    assert any("changed" in text for text in messages_of(response))
    assert VendorClaim.objects.get(vendor=vendor).status == VendorClaim.Status.SUBMITTED
    assert not VendorMember.objects.filter(vendor=vendor).exists()


def test_the_change_page_shows_the_claim_status(staff_client: Client) -> None:
    vendor = VendorFactory.create(italian_company=True)
    submitted_claim(vendor)
    page = staff_client.get(reverse("admin:vendors_vendor_change", args=[vendor.pk]))
    assert "Submitted, waiting for approval" in page.content.decode()

    locked = VendorClaimFactory.create(status=VendorClaim.Status.LOCKED, version=3)
    page = staff_client.get(reverse("admin:vendors_vendor_change", args=[locked.vendor.pk]))
    assert "Locked after too many wrong answers (link version 3)" in page.content.decode()


@pytest.mark.parametrize("action", ["approve_claim", "reject_claim"])
def test_deciding_a_claim_needs_a_fresh_second_factor(
    staff_client: Client, settings: SettingsWrapper, action: str
) -> None:
    settings.MFA_STEP_UP_MAX_AGE = -1  # no verification counts as fresh
    vendor = VendorFactory.create(italian_company=True)
    claim = submitted_claim(vendor)
    response = staff_client.post(
        reverse(CHANGELIST),
        {"action": action, "_selected_action": [vendor.pk], "post": "yes", **reviewed(claim)},
    )
    assert response.status_code == 302
    assert urlsplit(response["Location"]).path == reverse("mfa:verify")
    claim.refresh_from_db()
    assert claim.status == VendorClaim.Status.SUBMITTED
    assert not VendorMember.objects.filter(vendor=vendor).exists()


def test_the_changelist_itself_needs_no_fresh_second_factor(
    staff_client: Client, settings: SettingsWrapper
) -> None:
    settings.MFA_STEP_UP_MAX_AGE = -1
    assert staff_client.get(reverse(CHANGELIST)).status_code == 200


def test_sending_a_claim_link_needs_a_fresh_second_factor(
    staff_client: Client, settings: SettingsWrapper
) -> None:
    settings.MFA_STEP_UP_MAX_AGE = -1
    vendor = VendorFactory.create(italian_company=True)
    response = staff_client.post(
        reverse(CHANGELIST), {"action": "send_claim_link", "_selected_action": [vendor.pk]}
    )
    assert response.status_code == 302
    assert urlsplit(response["Location"]).path == reverse("mfa:verify")
    assert not OutboxEmail.objects.exists()


# A duplicated `action` must not slip past the step-up: Django runs
# `getlist("action")[index]` (the first value by default), whatever the last one is.
DUPLICATED_ACTIONS = [
    pytest.param(lambda name: {"action": [name, "x"]}, id="first"),
    pytest.param(lambda name: {"action": ["x", name], "index": "1"}, id="second-indexed"),
    pytest.param(lambda name: {"action": [name, "x"], "index": "0"}, id="first-indexed"),
    pytest.param(lambda name: {"action": ["", name], "index": "1"}, id="blank-first"),
    pytest.param(lambda name: {"action": [name, "delete_selected"]}, id="other-action"),
]


@pytest.mark.parametrize("variant", DUPLICATED_ACTIONS)
@pytest.mark.parametrize("action", ["approve_claim", "reject_claim"])
def test_a_duplicated_action_does_not_bypass_the_step_up_for_decisions(
    staff_client: Client,
    settings: SettingsWrapper,
    action: str,
    variant: Callable[[str], dict[str, Any]],
) -> None:
    settings.MFA_STEP_UP_MAX_AGE = -1
    vendor = VendorFactory.create(italian_company=True)
    claim = submitted_claim(vendor)
    data = {**variant(action), "_selected_action": [vendor.pk], "post": "yes", **reviewed(claim)}
    response = staff_client.post(reverse(CHANGELIST), data)
    assert response.status_code == 302
    assert urlsplit(response["Location"]).path == reverse("mfa:verify")
    claim.refresh_from_db()
    assert claim.status == VendorClaim.Status.SUBMITTED
    assert not VendorMember.objects.filter(vendor=vendor).exists()
    assert not AuditLog.objects.filter(
        action__in=[services.CLAIM_APPROVED, services.CLAIM_REJECTED]
    ).exists()


@pytest.mark.parametrize("variant", DUPLICATED_ACTIONS)
def test_a_duplicated_action_does_not_bypass_the_step_up_for_sending(
    staff_client: Client, settings: SettingsWrapper, variant: Callable[[str], dict[str, Any]]
) -> None:
    settings.MFA_STEP_UP_MAX_AGE = -1
    vendor = VendorFactory.create(italian_company=True)
    data = {**variant("send_claim_link"), "_selected_action": [vendor.pk]}
    response = staff_client.post(reverse(CHANGELIST), data)
    assert response.status_code == 302
    assert urlsplit(response["Location"]).path == reverse("mfa:verify")
    assert not OutboxEmail.objects.exists()
    assert not VendorClaim.objects.filter(vendor=vendor).exists()


@pytest.mark.parametrize("name", ["send_claim_link", "approve_claim", "reject_claim"])
def test_each_claim_action_checks_the_step_up_itself(
    rf: RequestFactory, superuser: User, settings: SettingsWrapper, name: str
) -> None:
    """Whatever the changelist does, the action itself refuses a stale second factor."""
    settings.MFA_STEP_UP_MAX_AGE = -1
    vendor = VendorFactory.create(italian_company=True)
    claim = submitted_claim(vendor) if name != "send_claim_link" else None
    data = {"post": "yes", **(reviewed(claim) if claim else {})}
    request = rf.post(reverse(CHANGELIST), data)
    request.user = superuser
    SessionMiddleware(lambda req: HttpResponse()).process_request(request)
    model_admin = VendorAdmin(Vendor, admin.site)
    action = cast(Any, model_admin.get_actions(request)[name])[0]
    response = action(model_admin, request, Vendor.objects.filter(pk=vendor.pk))
    assert response is not None
    if name == "approve_claim":  # critical: a session with no the passkey window is refused
        assert response.status_code == 403
        return
    assert response.status_code == 302
    assert urlsplit(response["Location"]).path in {reverse("mfa:verify"), reverse("mfa:setup")}
    assert not VendorMember.objects.filter(vendor=vendor).exists()
    outbox = OutboxEmail.objects.count()
    assert outbox == (0 if claim is None else 1)  # only submitted_claim's own link
    if claim is not None:
        claim.refresh_from_db()
        assert claim.status == VendorClaim.Status.SUBMITTED


# --- The send / regenerate dialog: legal email of record, or the PEC on file


def dialog_url(vendor: Vendor) -> str:
    return reverse("admin:vendors_vendor_claim_link", args=[vendor.pk])


def test_the_change_page_links_to_the_send_dialog(staff_client: Client) -> None:
    vendor = VendorFactory.create(italian_company=True)
    page = staff_client.get(reverse("admin:vendors_vendor_change", args=[vendor.pk]))
    assert dialog_url(vendor) in page.content.decode()


def test_the_dialog_offers_the_legal_email_and_the_pec_only(staff_client: Client) -> None:
    vendor = VendorFactory.create(italian_company=True)
    page = staff_client.get(dialog_url(vendor))
    assert page.status_code == 200
    choices = [value for value, _label in page.context["form"].fields["channel"].choices]
    assert choices == [engine.CLAIM_CHANNEL_EMAIL, engine.CLAIM_CHANNEL_PEC]
    html = page.content.decode()
    assert vendor.contact_email in html
    assert vendor.pec in html


def test_without_a_pec_the_dialog_offers_the_legal_email_only(staff_client: Client) -> None:
    vendor = VendorFactory.create(italian_company=True, pec="")
    page = staff_client.get(dialog_url(vendor))
    choices = [value for value, _label in page.context["form"].fields["channel"].choices]
    assert choices == [engine.CLAIM_CHANNEL_EMAIL]
    response = staff_client.post(dialog_url(vendor), {"channel": engine.CLAIM_CHANNEL_PEC})
    assert response.status_code == 200
    assert not OutboxEmail.objects.exists()


def test_staff_send_the_link_to_the_pec_from_the_dialog(staff_client: Client) -> None:
    vendor = VendorFactory.create(italian_company=True)
    response = staff_client.post(dialog_url(vendor), {"channel": engine.CLAIM_CHANNEL_PEC})
    assert response.status_code == 302
    assert OutboxEmail.objects.get().recipient == vendor.pec
    entry = AuditLog.objects.get(action=services.CLAIM_LINK_SENT)
    assert (entry.after or {})["channel"] == engine.CLAIM_CHANNEL_PEC
    assert entry.source == AuditLog.Source.ADMIN


def test_the_dialog_takes_no_other_address(staff_client: Client) -> None:
    vendor = VendorFactory.create(italian_company=True)
    response = staff_client.post(
        dialog_url(vendor), {"channel": "attacker@example.com", "email": "attacker@example.com"}
    )
    assert response.status_code == 200
    assert not OutboxEmail.objects.exists()


def test_the_dialog_needs_a_fresh_second_factor(
    staff_client: Client, settings: SettingsWrapper
) -> None:
    settings.MFA_STEP_UP_MAX_AGE = -1
    vendor = VendorFactory.create(italian_company=True)
    response = staff_client.post(dialog_url(vendor), {"channel": engine.CLAIM_CHANNEL_EMAIL})
    assert response.status_code == 302
    assert urlsplit(response["Location"]).path == reverse("mfa:verify")
    assert not OutboxEmail.objects.exists()


def test_staff_restart_a_rejected_claim_from_the_dialog(staff_client: Client) -> None:
    vendor = VendorFactory.create(italian_company=True)
    claim = submitted_claim(vendor)
    decide(staff_client, "reject_claim", vendor)
    response = staff_client.post(dialog_url(vendor), {"channel": engine.CLAIM_CHANNEL_EMAIL})
    assert response.status_code == 302
    claim.refresh_from_db()
    assert claim.status == VendorClaim.Status.OPEN
    entry = AuditLog.objects.get(action=services.CLAIM_LINK_REGENERATED)
    assert (entry.after or {})["after_reject"]["claim"] == str(claim.public_id)


def test_a_dialog_error_is_shown_not_raised(staff_client: Client) -> None:
    vendor = VendorFactory.create(italian_company=True)
    VendorMemberFactory.create(vendor=vendor)
    response = staff_client.post(dialog_url(vendor), {"channel": engine.CLAIM_CHANNEL_EMAIL})
    assert response.status_code == 200
    assert "account holder" in response.content.decode()
    assert not OutboxEmail.objects.exists()
