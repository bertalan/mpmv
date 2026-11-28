"""SEC.5 attack scenarios, category "Accounts" (owner security wave, item 10).

Each test plays the attacker against our own login, MFA, step-up and session handling, and
asserts that the attack fails and leaves an audit trace where one is expected. Cases an existing
test already covers are not repeated here: tests/security/README.md names the covering test.

The only sign-in page today is the Django admin login (`MFA_LOGIN_URL`); the customer login,
registration, password reset and vendor claim pages arrive with their own tasks.
"""

import secrets
import time
from collections.abc import Callable
from typing import Any

import pytest
from django.conf import settings
from django.contrib.auth.hashers import get_hasher
from django.contrib.sessions.backends.db import SessionStore
from django.test import Client
from django.urls import reverse

from apps.accounts import services
from apps.accounts.mfa_session import VERIFIED_AT_SESSION_KEY
from apps.accounts.models import User
from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.integration.urls import APPROVE_PURPOSE, DOCUMENTS, Document
from apps.accounts.tests.mfa import password_login, totp_token, verified_login
from apps.core.audit import AuditContext
from apps.core.models import AuditLog

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("admin_open"),  # OPS.5: the staff admin is loopback-only by default
    pytest.mark.urls("apps.accounts.tests.integration.urls"),
]

LOGIN_URL = "/django-admin/login/"
LOGOUT_URL = "/django-admin/logout/"
IP = "203.0.113.7"
GENERIC_ERROR = "Please enter the correct email and password for a staff account."
SENSITIVE = "/test/sensitive/"  # step_up_required(): the 300 s window of spec 02
VERY_SENSITIVE = "/test/very-sensitive/"  # step_up_required(max_age=30)
SYSTEM = AuditContext(actor_label="system:test", source=AuditLog.Source.SYSTEM)


def _password() -> str:
    return secrets.token_urlsafe(16)  # generated: no credential literals in git


def _login(client: Client, email: str, password: str, *, ip: str = IP) -> Any:
    return client.post(
        LOGIN_URL,
        {"username": email, "password": password, "next": "/django-admin/"},
        REMOTE_ADDR=ip,
    )


def _signed_in(client: Client) -> bool:
    """Whether the client's session still authenticates someone, after one more request."""
    client.get("/")  # an invalid session auth hash is flushed on the next request
    return "_auth_user_id" in client.session


def _session_is_alive(session_key: str) -> bool:
    return bool(SessionStore().exists(session_key))


def _replay_cookie(session_key: str) -> Client:
    """An attacker's browser holding a copied session cookie."""
    attacker = Client()
    attacker.cookies[settings.SESSION_COOKIE_NAME] = session_key
    return attacker


def _planted_session(client: Client) -> str:
    """A session the attacker created and planted in the victim's browser before sign-in."""
    session = client.session
    session["planted"] = True
    session.save()
    key = session.session_key
    assert key is not None
    client.cookies[settings.SESSION_COOKIE_NAME] = key
    return key


# --- Account enumeration through login: by timing


@pytest.fixture
def hash_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Spy on the password hasher: one entry per password hash computed.

    A structural timing check: the expensive step of a login is the hash, so an unknown email
    must hash once like a real one (Django hashes a throwaway password for it). Counting calls
    instead of measuring wall-clock time keeps the test fast and never flaky.
    """
    hasher_class = type(get_hasher())
    original = hasher_class.encode
    calls: list[str] = []

    def spy(self: Any, password: str, salt: str, *args: Any, **kwargs: Any) -> str:
        calls.append(salt)
        return str(original(self, password, salt, *args, **kwargs))

    monkeypatch.setattr(hasher_class, "encode", spy)
    return calls


def _attempt_cost(client: Client, email: str, calls: list[str]) -> tuple[int, int, bool]:
    """(hashes computed, status code, generic error shown) for one wrong-password login."""
    calls.clear()
    response = _login(client, email, _password())
    shown = GENERIC_ERROR in response.content.decode() if response.status_code == 200 else False
    return len(calls), response.status_code, shown


def _locked_attempt(
    client: Client, email: str, calls: list[str]
) -> tuple[int, int, tuple[str, ...]]:
    """(hashes computed, status code, form errors shown) for one login on a locked account."""
    calls.clear()
    response = _login(client, email, _password())
    form = response.context["form"]
    return len(calls), response.status_code, tuple(form.non_field_errors())


def test_login_does_the_same_work_whether_or_not_the_account_exists(
    client: Client, hash_calls: list[str]
) -> None:
    UserFactory.create(email="staff@example.com", staff=True)
    UserFactory.create(email="customer@example.com")
    UserFactory.create(email="gone@example.com", staff=True, is_active=False)
    costs = {
        email: _attempt_cost(client, email, hash_calls)
        for email in (
            "staff@example.com",
            "customer@example.com",
            "gone@example.com",
            "nobody@example.com",
        )
    }
    assert set(costs.values()) == {(1, 200, True)}, costs
    assert AuditLog.objects.filter(action=services.LOGIN_FAILED).count() == len(costs)


def test_a_locked_login_does_the_same_work_whether_or_not_the_account_exists(
    client: Client, hash_calls: list[str]
) -> None:
    UserFactory.create(email="staff@example.com", staff=True)
    for email in ("staff@example.com", "nobody@example.com"):
        for _ in range(services.ACCOUNT_LOCKOUT.free_attempts):
            _login(client, email, _password(), ip=f"198.51.100.{len(email)}")
    locked = {
        email: _locked_attempt(client, email, hash_calls)
        for email in ("staff@example.com", "nobody@example.com")
    }
    # Refused before any password check, for a real account and an unknown one alike, with the
    # same waiting-time text (CORE.29), not the generic wrong-password error.
    assert len(set(locked.values())) == 1, locked
    hashes, status, errors = locked["staff@example.com"]
    assert (hashes, status) == (0, 200)
    assert errors
    assert GENERIC_ERROR not in errors
    assert AuditLog.objects.filter(action=services.LOCKED_OUT).count() == 2


@pytest.mark.skip(
    reason="No registration, password reset or vendor claim page exists yet (customer accounts, "
    "CORE.19, VEN.24); their enumeration cases belong to those tasks. CORE.19's done-when "
    "already asks for the same response and timing class for unknown emails."
)
def test_registration_reset_and_claim_do_not_reveal_accounts() -> None:
    """Placeholder so the gap shows in every test report until those pages exist."""


# --- Credential stuffing across accounts from one IP


def test_credential_stuffing_from_one_ip_is_locked_out_and_audited(client: Client) -> None:
    password = _password()
    victim = UserFactory.create(email="victim@example.com", staff=True, password=password)
    for i in range(services.IP_LOCKOUT.free_attempts):
        _login(client, f"leaked{i}@example.com", _password())  # one guess per leaked account

    response = _login(client, victim.email, password)  # a valid pair from the same list
    assert response.status_code == 200
    assert "_auth_user_id" not in client.session
    assert not AuditLog.objects.filter(action=services.LOGIN_SUCCEEDED).exists()

    lock = AuditLog.objects.get(action=services.LOCKED_OUT, target_key=f"login:ip:{IP}")
    assert lock.ip == IP
    assert lock.after is not None
    assert lock.after["failures"] == services.IP_LOCKOUT.free_attempts
    failed = AuditLog.objects.filter(action=services.LOGIN_FAILED, ip=IP)
    assert failed.count() == services.IP_LOCKOUT.free_attempts
    # What was typed for unknown accounts is never stored.
    assert set(failed.values_list("target_key", flat=True)) == {services.UNKNOWN_ACCOUNT}


# --- Step-up reuse across actions


def test_one_step_up_does_not_open_an_action_with_a_shorter_window(client: Client) -> None:
    user = UserFactory.create(staff=True)
    verified_login(client, user, verified_at=time.time() - 60)
    assert client.get(SENSITIVE).status_code == 200  # inside the 300 s window (spec 02)
    response = client.get(VERY_SENSITIVE)
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("mfa:verify"))


def test_a_step_up_does_not_carry_over_to_another_account_in_the_same_browser(
    client: Client,
) -> None:
    first = UserFactory.create(staff=True)
    verified_login(client, first)
    assert client.get(SENSITIVE).status_code == 200

    password = _password()
    second = UserFactory.create(email="second@example.com", staff=True, password=password)
    TOTPDeviceFactory.create(user=second)
    assert _login(client, second.email, password).status_code == 302
    assert client.session["_auth_user_id"] == str(second.pk)
    assert VERIFIED_AT_SESSION_KEY not in client.session
    response = client.get(SENSITIVE)
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("mfa:verify"))


# --- Session fixation


def test_a_planted_session_id_is_worthless_after_the_password_login(client: Client) -> None:
    password = _password()
    user = UserFactory.create(email="staff@example.com", staff=True, password=password)
    planted = _planted_session(client)

    assert _login(client, user.email, password).status_code == 302
    assert client.session.session_key != planted
    assert not _session_is_alive(planted)
    assert not _signed_in(_replay_cookie(planted))
    assert AuditLog.objects.filter(action=services.LOGIN_SUCCEEDED).count() == 1


def test_the_pre_mfa_session_id_is_worthless_after_the_second_factor(client: Client) -> None:
    user = UserFactory.create(staff=True)
    device = TOTPDeviceFactory.create(user=user)
    password_login(client, user)
    before_mfa = client.session.session_key
    assert before_mfa is not None

    response = client.post(reverse("mfa:verify"), {"method": "totp", "token": totp_token(device)})
    assert response.status_code == 302
    assert client.session.session_key != before_mfa
    assert not _session_is_alive(before_mfa)
    assert AuditLog.objects.filter(action=services.VERIFIED, actor_id=user.pk).exists()


def test_a_planted_session_id_is_worthless_after_an_action_link_sign_in(client: Client) -> None:
    user = UserFactory.create()
    TOTPDeviceFactory.create(user=user)
    DOCUMENTS["doc-fix"] = Document(owner_id=user.pk, version="1")
    try:
        token = services.issue_action_token(
            purpose=APPROVE_PURPOSE, object_key="doc-fix", recipient=user, version="1"
        )
        planted = _planted_session(client)
        response = client.post(reverse("action_link:continue"), {"token": token})
    finally:
        DOCUMENTS.pop("doc-fix", None)
    assert response.status_code == 302
    assert client.session["_auth_user_id"] == str(user.pk)
    assert client.session.session_key != planted
    assert not _session_is_alive(planted)
    assert AuditLog.objects.filter(action=services.ACTION_LINK_SIGNED_IN).count() == 1


# --- Sessions after logout and after a password, MFA or email change


def test_a_copied_session_cookie_is_dead_after_logout(client: Client) -> None:
    user = UserFactory.create(staff=True)
    verified_login(client, user)
    stolen = client.session.session_key
    assert stolen is not None
    assert _signed_in(_replay_cookie(stolen))

    assert client.post(LOGOUT_URL).status_code == 200
    assert not _session_is_alive(stolen)
    assert not _signed_in(_replay_cookie(stolen))


@pytest.fixture
def victim_sessions() -> Callable[[User], Client]:
    """Open an extra signed-in session of a user, as on a stolen or forgotten device."""

    def open_session(user: User) -> Client:
        other = Client()
        verified_login(other, user)
        assert _signed_in(other)
        return other

    return open_session


def test_a_password_set_by_staff_ends_the_users_other_sessions(
    client: Client, superuser: User, victim_sessions: Callable[[User], Client]
) -> None:
    victim = UserFactory.create(email="customer@example.com")
    stolen = victim_sessions(victim)
    verified_login(client, superuser)
    password = _password()
    response = client.post(
        reverse("admin:auth_user_password_change", args=[victim.pk]),
        {"usable_password": "true", "password1": password, "password2": password},
    )
    assert response.status_code == 302, response.content.decode()
    assert not _signed_in(stolen)
    assert AuditLog.objects.filter(action=services.CREDENTIAL_CHANGED).count() == 1


def test_changing_ones_own_password_ends_the_other_sessions_only(
    client: Client, victim_sessions: Callable[[User], Client]
) -> None:
    old_password = _password()
    user = UserFactory.create(email="staff@example.com", superuser=True, password=old_password)
    stolen = victim_sessions(user)
    verified_login(client, user)
    new_password = _password()
    response = client.post(
        reverse("admin:password_change"),
        {
            "old_password": old_password,
            "new_password1": new_password,
            "new_password2": new_password,
        },
    )
    assert response.status_code == 302, response.content.decode()
    assert not _signed_in(stolen)
    assert _signed_in(client)
    assert AuditLog.objects.filter(action=services.CREDENTIAL_CHANGED).count() == 1


def test_an_email_change_ends_the_users_other_sessions(
    victim_sessions: Callable[[User], Client],
) -> None:
    victim = UserFactory.create(email="customer@example.com")
    stolen = victim_sessions(victim)
    victim.email = "customer.new@example.com"
    services.save_user(victim, ctx=SYSTEM)
    assert AuditLog.objects.filter(action=services.USER_UPDATED).count() == 1
    assert not _signed_in(stolen)


def test_an_mfa_reset_ends_the_users_sessions(
    victim_sessions: Callable[[User], Client],
) -> None:
    victim = UserFactory.create(staff=True)
    stolen = victim_sessions(victim)
    services.reset_mfa(victim, ctx=SYSTEM)
    assert AuditLog.objects.filter(action=services.DISABLED).count() == 1
    assert stolen.get(reverse("mfa:totp")).status_code != 200
    assert not _signed_in(stolen)


# --- Privilege escalation


@pytest.mark.skip(
    reason="Support impersonation (CORE.10) is not implemented yet; its done-when lists the "
    "escalation cases (staff or superuser targets, hard floor, view-as writes)."
)
def test_impersonation_cannot_escalate_privileges() -> None:
    """Placeholder so the gap shows in every test report until CORE.10 lands."""
