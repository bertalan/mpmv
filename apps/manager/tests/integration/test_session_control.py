"""CORE.24 part 1: Support's "End this user's sessions" and the owner's "End every session".

Both are critical (CORE.25): a fresh passkey each time, even with the passkey window open. Real
sessions in PostgreSQL; the panic alert goes through the OPS.2 outbox. Needs human review:
authentication code.
"""

from typing import Any

import pytest
from django.contrib.auth import SESSION_KEY
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.utils.translation import override

from apps.accounts import levels, services
from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.core.audit import AuditContext
from apps.core.models import AuditLog, OutboxEmail
from apps.vendors.tests.factories import VendorMemberFactory

pytestmark = pytest.mark.django_db

USER_SESSIONS = "/manager/platform/sessions/"
PANIC = "/manager/platform/sessions/end-every-session/"
WORD = "END EVERY SESSION"
CTX = AuditContext(actor_label="test", source=AuditLog.Source.ADMIN)


def signed_in_client(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def is_signed_in(client: Client) -> bool:
    return bool(client.session.get(SESSION_KEY))


def ended_entry() -> dict[str, Any]:
    """The `after` of the one "sessions ended" audit entry."""
    after = AuditLog.objects.get(action=services.SESSIONS_ENDED).after
    assert isinstance(after, dict)
    return after


def staff_client(role: str, **login: Any) -> tuple[Client, User]:
    user = UserFactory.create(platform_role=role)
    client = Client()
    verified_login(client, user, **login)
    return client, user


# --- Support: "End this user's sessions"


def test_support_sees_a_users_sessions_and_ends_them_with_a_reason() -> None:
    client, support = staff_client("support", passkey=True)
    target = UserFactory.create(email="buyer@example.com")
    victim = signed_in_client(target)
    page = client.get(USER_SESSIONS, {"email": "BUYER@example.com"})
    assert page.status_code == 200
    assert b"End this user" in page.content
    response = client.post(
        USER_SESSIONS, {"user": str(target.public_id), "reason": "Suspected account takeover"}
    )
    assert response.status_code == 302
    assert str(target.public_id) in response["Location"]
    assert "buyer" not in response["Location"]  # no email in the URL
    assert not is_signed_in(victim)
    assert is_signed_in(client)
    entry = AuditLog.objects.get(action=services.SESSIONS_ENDED)
    assert entry.actor_id == support.pk
    assert ended_entry()["reason"] == "support"
    assert ended_entry()["note"] == "Suspected account takeover"


def test_support_needs_a_reason() -> None:
    client, _ = staff_client("support", passkey=True)
    target = UserFactory.create()
    victim = signed_in_client(target)
    response = client.post(USER_SESSIONS, {"user": str(target.public_id), "reason": "  "})
    assert response.status_code == 400
    assert is_signed_in(victim)


def test_support_without_a_fresh_passkey_is_refused_even_with_the_window_open() -> None:
    client, _ = staff_client("support")  # window open, verified with TOTP only
    target = UserFactory.create()
    victim = signed_in_client(target)
    response = client.post(USER_SESSIONS, {"user": str(target.public_id), "reason": "Takeover"})
    assert response.status_code == 302
    assert "/account/mfa/verify/" in response["Location"]
    assert "factor=passkey" in response["Location"]
    assert is_signed_in(victim)
    assert not AuditLog.objects.filter(action=services.SESSIONS_ENDED).exists()


def test_support_with_no_window_gets_the_passkey_prompt() -> None:
    client, _ = staff_client("support", passkey=True, level=levels.NO_WINDOW)
    target = UserFactory.create()
    victim = signed_in_client(target)
    response = client.post(USER_SESSIONS, {"user": str(target.public_id), "reason": "Takeover"})
    assert response.status_code == 403
    assert is_signed_in(victim)


@pytest.mark.parametrize("role", ["finance", "catalog", "config"])
def test_other_platform_roles_do_not_reach_the_user_sessions_page(role: str) -> None:
    client, _ = staff_client(role, passkey=True)
    target = UserFactory.create()
    victim = signed_in_client(target)
    assert client.get(USER_SESSIONS).status_code == 404
    response = client.post(USER_SESSIONS, {"user": str(target.public_id), "reason": "x"})
    assert response.status_code == 404
    assert is_signed_in(victim)


def test_the_service_refuses_an_actor_without_the_support_role() -> None:
    actor, target = UserFactory.create(staff=True), UserFactory.create()
    with pytest.raises(PermissionDenied):
        services.support_end_sessions(target, actor=actor, reason="x", ctx=CTX)


def test_the_support_service_dry_run_ends_nothing() -> None:
    support, target = UserFactory.create(platform_role="support"), UserFactory.create()
    victim = signed_in_client(target)
    changes = services.support_end_sessions(
        target, actor=support, reason="Check", ctx=CTX, dry_run=True
    )
    assert not changes.is_empty
    assert is_signed_in(victim)
    assert not AuditLog.objects.filter(action=services.SESSIONS_ENDED).exists()


# --- The platform owner: "End every session"


def test_the_panic_button_keeps_the_caller_and_ends_every_other_session(settings: Any) -> None:
    settings.OPS_ALERT_EMAIL = "oncall@platform.example"
    client, owner = staff_client("owner", passkey=True)
    owners_laptop = signed_in_client(owner)
    customer = signed_in_client(UserFactory.create())
    vendor = signed_in_client(VendorMemberFactory.create().user)
    assert client.get(PANIC).status_code == 200
    response = client.post(PANIC, {"confirm": WORD})
    assert response.status_code == 302
    assert is_signed_in(client)
    assert client.get(PANIC).status_code == 200  # still signed in and verified
    assert not is_signed_in(owners_laptop)
    assert not is_signed_in(customer)
    assert not is_signed_in(vendor)
    entry = AuditLog.objects.get(action=services.ALL_SESSIONS_ENDED)
    assert entry.actor_id == owner.pk
    assert entry.before == {"sessions": 3}
    assert entry.after == {"sessions": 0, "kept": True}
    mail = OutboxEmail.objects.get(recipient="oncall@platform.example")
    assert services.PANIC_ALERT in (mail.idempotency_key or "")


def test_the_panic_button_needs_the_typed_word() -> None:
    client, _ = staff_client("owner", passkey=True)
    other = signed_in_client(UserFactory.create())
    response = client.post(PANIC, {"confirm": "end every session please"})
    assert response.status_code == 400
    assert is_signed_in(other)
    assert not AuditLog.objects.filter(action=services.ALL_SESSIONS_ENDED).exists()


def test_the_typed_word_is_in_the_language_of_the_page() -> None:
    with override("it"):
        from django.utils.translation import gettext

        assert gettext(WORD) == "TERMINA OGNI SESSIONE"


def test_the_panic_button_without_a_fresh_passkey_is_refused() -> None:
    client, _ = staff_client("owner")  # window open, TOTP only
    other = signed_in_client(UserFactory.create())
    response = client.post(PANIC, {"confirm": WORD})
    assert response.status_code == 302
    assert "factor=passkey" in response["Location"]
    assert is_signed_in(other)


def test_the_panic_button_with_no_window_gets_the_passkey_prompt() -> None:
    client, _ = staff_client("owner", passkey=True, level=levels.NO_WINDOW)
    other = signed_in_client(UserFactory.create())
    assert client.post(PANIC, {"confirm": WORD}).status_code == 403
    assert is_signed_in(other)


@pytest.mark.parametrize("role", ["support", "finance", "catalog", "config"])
def test_no_other_platform_role_reaches_the_panic_button(role: str) -> None:
    client, _ = staff_client(role, passkey=True)
    other = signed_in_client(UserFactory.create())
    assert client.get(PANIC).status_code == 404
    assert client.post(PANIC, {"confirm": WORD}).status_code == 404
    assert is_signed_in(other)


def test_a_vendor_member_is_refused_both_actions() -> None:
    member = VendorMemberFactory.create(role="owner").user
    client = Client()
    verified_login(client, member, passkey=True)
    target = UserFactory.create()
    victim = signed_in_client(target)
    assert client.post(PANIC, {"confirm": WORD}).status_code in (403, 404)
    response = client.post(USER_SESSIONS, {"user": str(target.public_id), "reason": "x"})
    assert response.status_code in (403, 404)
    assert is_signed_in(victim)
    assert not AuditLog.objects.filter(action__startswith="accounts.sessions.").exists()


@pytest.mark.parametrize(
    "actor",
    [
        lambda: UserFactory.create(platform_role="support"),
        lambda: UserFactory.create(superuser=True),  # break-glass, no role: refused too
        lambda: VendorMemberFactory.create(role="owner").user,
    ],
    ids=["support", "bare-superuser", "vendor-owner"],
)
def test_the_panic_service_refuses_everyone_but_the_platform_owner(actor: Any) -> None:
    other = signed_in_client(UserFactory.create())
    with pytest.raises(PermissionDenied):
        services.end_all_sessions(actor=actor(), keep_session_key=None, ctx=CTX)
    assert is_signed_in(other)


def test_the_panic_service_dry_run_counts_and_ends_nothing() -> None:
    owner = UserFactory.create(platform_role="owner")
    other = signed_in_client(UserFactory.create())
    changes = services.end_all_sessions(actor=owner, keep_session_key=None, ctx=CTX, dry_run=True)
    assert changes.changes[0].before == {"sessions": 1}
    assert is_signed_in(other)
    assert not AuditLog.objects.filter(action=services.ALL_SESSIONS_ENDED).exists()


# --- Role and membership changes end the sessions of the account changed


def test_a_role_grant_ends_the_targets_sessions() -> None:
    from apps.manager import staff_roles

    owner, target = UserFactory.create(platform_role="owner"), UserFactory.create(staff=True)
    victim = signed_in_client(target)
    staff_roles.grant_role(target, "support", actor=owner, ctx=CTX)
    assert not is_signed_in(victim)
    assert ended_entry()["reason"] == "role"


def test_a_membership_change_ends_the_members_sessions() -> None:
    from apps.vendors import services as vendor_services

    member = VendorMemberFactory.create(role="staff")
    VendorMemberFactory.create(vendor=member.vendor, role="owner")
    victim = signed_in_client(member.user)
    vendor_services.change_member_role(member, "owner", ctx=CTX)
    assert not is_signed_in(victim)
    other = VendorMemberFactory.create(role="staff")
    VendorMemberFactory.create(vendor=other.vendor, role="owner")
    removed = signed_in_client(other.user)
    vendor_services.remove_member(other, ctx=CTX)
    assert not is_signed_in(removed)
