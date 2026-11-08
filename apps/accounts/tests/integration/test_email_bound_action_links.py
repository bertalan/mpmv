"""VEN.24 on top of CORE.15: secure action links bound to an email address, not to a user.

A vendor imported without an account holder has no user to bind a link to. Its purpose names
the email address of record instead (`ActionPurpose.email_of`): the link goes only there, a GET
changes nothing, the Continue POST signs nobody in and only opens the link in the session for
the action page, and the single use is spent by the app when the action completes.
"""

import json
import time
from collections.abc import Iterator
from typing import Any

import pytest
from django.contrib.sessions.backends.base import SessionBase
from django.test import Client
from django.urls import reverse
from django_otp.oath import TOTP
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts import engine, services
from apps.accounts.models import ActionLinkUse, RecoveryCodeDevice
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.integration.urls import INVITATIONS, INVITE_PURPOSE, Invitation
from apps.core.audit import AuditContext
from apps.core.models import AuditLog, OutboxEmail

pytestmark = [pytest.mark.django_db, pytest.mark.urls("apps.accounts.tests.integration.urls")]

SYSTEM = AuditContext(actor_label="system:test", source=AuditLog.Source.SYSTEM)
EMAIL = "legal@example.com"


@pytest.fixture(autouse=True)
def _invitations() -> Iterator[None]:
    INVITATIONS.clear()
    INVITATIONS["inv-1"] = Invitation(email=EMAIL, version="1")
    yield
    INVITATIONS.clear()


def invite_token(**kwargs: Any) -> str:
    return services.issue_action_token(
        purpose=INVITE_PURPOSE, object_key="inv-1", version="1", **kwargs
    )


def press_continue(client: Client, token: str) -> Any:
    return client.post(reverse("action_link:continue"), {"token": token})


def signed_in(client: Client) -> bool:
    return "_auth_user_id" in client.session


def session_of(client: Client) -> SessionBase:
    return client.session


def test_an_email_bound_link_carries_no_readable_address() -> None:
    token = invite_token()
    assert "legal" not in token
    check = services.check_action_token(token)
    assert check.refusal is None
    assert check.email_bound
    assert check.user is None
    assert check.claims is not None
    assert check.claims.recipient == engine.email_recipient(EMAIL)


def test_an_email_bound_purpose_takes_no_recipient_user() -> None:
    with pytest.raises(ValueError, match="email"):
        invite_token(recipient=UserFactory.create())


def test_a_user_bound_purpose_needs_a_recipient() -> None:
    with pytest.raises(ValueError, match="recipient"):
        services.issue_action_token(purpose="tests.approve", object_key="doc-1", version="1")


def test_the_link_goes_only_to_the_email_of_record() -> None:
    services.send_action_link_email(
        purpose=INVITE_PURPOSE,
        object_key="inv-1",
        version="1",
        template_key="tests.reminder",
        context={"item": "the invitation", "due": "Friday"},
        ctx=SYSTEM,
    )
    email = OutboxEmail.objects.get()
    assert email.recipient == EMAIL
    action_url = json.loads(email.context)["action_url"]
    token = action_url.rstrip("/").rsplit("/", 1)[-1]
    assert services.check_action_token(token).refusal is None
    entry = AuditLog.objects.get(action=services.ACTION_LINK_SENT)
    assert entry.target_key == "inv-1"
    assert token not in json.dumps(entry.after)
    assert EMAIL not in json.dumps(entry.after)


def test_no_email_of_record_means_no_link() -> None:
    INVITATIONS["inv-1"].email = ""
    with pytest.raises(ValueError, match="email"):
        invite_token()


@pytest.mark.parametrize("method", ["get", "head"])
def test_get_and_head_change_nothing(client: Client, method: str) -> None:
    token = invite_token()
    response = getattr(client, method)(services.action_link_path(token))
    assert response.status_code == 200
    assert not signed_in(client)
    assert services.ACTION_LINK_SESSION_KEY not in client.session
    assert not AuditLog.objects.exists()
    assert not ActionLinkUse.objects.exists()


def test_continue_opens_the_link_in_the_session_and_signs_nobody_in(client: Client) -> None:
    token = invite_token()
    response = press_continue(client, token)
    assert response.status_code == 302
    assert response["Location"] == reverse("test-invite", args=["inv-1"])
    assert not signed_in(client)
    assert not ActionLinkUse.objects.exists()  # spent only when the action completes
    page = client.get(response["Location"])
    assert page.content == b"invitation inv-1"
    # The session keeps the verified claims, never the token itself.
    assert token not in json.dumps(dict(client.session.items()), default=str)


def test_the_action_page_without_an_opened_link_is_refused(client: Client) -> None:
    response = client.get(reverse("test-invite", args=["inv-1"]))
    assert response.status_code == 403


def test_an_opened_link_counts_only_for_its_own_object(client: Client) -> None:
    INVITATIONS["inv-2"] = Invitation(email=EMAIL, version="1")
    press_continue(client, invite_token())
    assert client.get(reverse("test-invite", args=["inv-2"])).status_code == 403


def test_a_new_email_of_record_invalidates_the_link(client: Client) -> None:
    token = invite_token()
    INVITATIONS["inv-1"].email = "new-legal@example.com"
    assert services.check_action_token(token).refusal == engine.WRONG_RECIPIENT
    response = press_continue(client, token)
    assert response["Location"] == reverse("test-invite", args=["inv-1"])
    assert client.get(response["Location"]).status_code == 403
    entry = AuditLog.objects.get(action=services.ACTION_LINK_REFUSED)
    assert (entry.after or {})["reason"] == engine.WRONG_RECIPIENT


def test_a_new_version_invalidates_an_already_opened_link(client: Client) -> None:
    press_continue(client, invite_token())
    INVITATIONS["inv-1"].version = "2"
    response = client.get(reverse("test-invite", args=["inv-1"]))
    assert response.status_code == 403
    assert response.content == f"refused {engine.OLD_VERSION}".encode()


def test_an_expired_link_is_refused(client: Client) -> None:
    token = invite_token(now=time.time() - 4 * 24 * 3600)
    assert services.check_action_token(token).refusal == engine.EXPIRED
    press_continue(client, token)
    assert client.get(reverse("test-invite", args=["inv-1"])).status_code == 403


def test_an_opened_link_expires_in_the_session_too(client: Client) -> None:
    token = invite_token(expires_in=60)
    press_continue(client, token)
    check = services.email_bound_link(
        session_of(client), INVITE_PURPOSE, "inv-1", now=time.time() + 120
    )
    assert check.refusal == engine.EXPIRED


def test_the_single_use_is_spent_once(client: Client) -> None:
    press_continue(client, invite_token())
    check = services.email_bound_link(session_of(client), INVITE_PURPOSE, "inv-1")
    user = UserFactory.create(email=EMAIL)
    services.spend_email_bound_link(check, user, ctx=SYSTEM)
    assert ActionLinkUse.objects.get().user == user
    assert AuditLog.objects.filter(action=services.ACTION_LINK_USED).count() == 1
    with pytest.raises(services.ActionLinkSpent):
        services.spend_email_bound_link(check, user, ctx=SYSTEM)
    assert services.email_bound_link(session_of(client), INVITE_PURPOSE, "inv-1").refusal == (
        engine.USED
    )


def test_a_signed_in_user_is_never_switched_by_an_email_bound_link(client: Client) -> None:
    user = UserFactory.create()
    client.force_login(user)
    response = press_continue(client, invite_token())
    assert response["Location"] == reverse("test-invite", args=["inv-1"])
    assert int(client.session["_auth_user_id"]) == user.pk


# --- An authenticator app enrolled before the account exists (VEN.24 claim page)


def code_for(key: str, *, offset_steps: int = 0) -> str:
    totp = TOTP(bytes.fromhex(key), 30, 0, 6, 0)
    totp.time = time.time() + offset_steps * 30
    return str(totp.token()).zfill(6)


def test_a_pending_totp_key_accepts_the_current_code_only() -> None:
    key = services.new_totp_key()
    assert services.totp_key_time_step(key, code_for(key)) is not None
    assert services.totp_key_time_step(key, code_for(key, offset_steps=5)) is None
    assert services.totp_key_time_step(key, "not a code") is None


def test_enrolling_a_pending_key_confirms_a_device_and_issues_recovery_codes() -> None:
    user = UserFactory.create()
    key = services.new_totp_key()
    step = services.totp_key_time_step(key, code_for(key))
    assert step is not None
    enrolment = services.enrol_totp_key(user, key, time_step=step, ctx=SYSTEM)
    device = TOTPDevice.objects.get(user=user)
    assert device.confirmed
    assert device.last_t == step  # the code used to enrol cannot be replayed
    assert enrolment.recovery_codes
    assert RecoveryCodeDevice.objects.filter(user=user).exists()
    assert services.has_second_factor(user)
    assert AuditLog.objects.filter(action=services.ENROLLED).count() == 1
