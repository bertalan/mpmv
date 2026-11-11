"""SEC.4: logins and second-factor verifications are in the audit trail (spec 12 SEC.7).

Failed attempts are audited only while they count (a lock refuses the rest before any password
check), and never with the submitted identifier of an unknown account: it may be a typo or a
password typed in the wrong field.
"""

import secrets

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.services import ACCOUNT_LOCKOUT
from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import password_login, totp_token
from apps.core.models import AuditLog

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("admin_open")]  # OPS.5

LOGIN_URL = "/django-admin/login/"
IP = "203.0.113.7"


@pytest.fixture
def password() -> str:
    return secrets.token_urlsafe(16)


@pytest.fixture
def staff(password: str) -> User:
    return UserFactory.create(email="staff@example.com", password=password, staff=True)


def _login(client: Client, email: str, pwd: str) -> None:
    client.post(
        LOGIN_URL,
        {"username": email, "password": pwd, "next": "/django-admin/"},
        REMOTE_ADDR=IP,
    )


def test_a_successful_login_is_audited(client: Client, staff: User, password: str) -> None:
    _login(client, "staff@example.com", password)
    entry = AuditLog.objects.get(action="accounts.login.succeeded")
    assert entry.target_model == "accounts.user"
    assert entry.target_key == str(staff.public_id)
    assert entry.actor_id == staff.pk
    assert entry.actor_label.startswith("staff:")
    assert "@" not in entry.actor_label
    assert entry.ip == IP


def test_a_failed_login_of_an_existing_account_is_audited_against_it(
    client: Client, staff: User
) -> None:
    _login(client, "Staff@Example.com", secrets.token_urlsafe(16))
    entry = AuditLog.objects.get(action="accounts.login.failed")
    assert entry.target_model == "accounts.user"
    assert entry.target_key == str(staff.public_id)
    assert entry.actor_id is None
    assert entry.actor_label == "anonymous"
    assert entry.ip == IP
    assert not AuditLog.objects.filter(action="accounts.login.succeeded").exists()


def test_a_failed_login_of_an_unknown_email_does_not_store_what_was_typed(
    client: Client,
) -> None:
    typed = f"{secrets.token_hex(6)}@example.com"
    _login(client, typed, secrets.token_urlsafe(16))
    entry = AuditLog.objects.get(action="accounts.login.failed")
    assert entry.target_key == "unknown"
    assert typed not in str(entry.before) + str(entry.after) + entry.target_key
    assert entry.ip == IP


def test_attempts_refused_during_a_lockout_are_not_audited_again(
    client: Client, staff: User, password: str
) -> None:
    # The failure that reaches `free_attempts` starts the lock; the next ones are refused.
    for _ in range(ACCOUNT_LOCKOUT.free_attempts + 3):
        _login(client, "staff@example.com", secrets.token_urlsafe(16))
    assert (
        AuditLog.objects.filter(action="accounts.login.failed").count()
        == ACCOUNT_LOCKOUT.free_attempts
    )
    assert AuditLog.objects.filter(action="accounts.login.locked_out").count() == 1


def test_a_verified_second_factor_is_audited(client: Client) -> None:
    user = UserFactory.create(staff=True)
    device = TOTPDeviceFactory.create(user=user)
    password_login(client, user)
    client.post(reverse("mfa:verify"), {"method": "totp", "token": totp_token(device)})
    entry = AuditLog.objects.get(action="accounts.mfa.verified")
    assert entry.target_key == str(user.public_id)
    assert entry.after == {"kind": "totp"}
    assert entry.actor_id == user.pk


def test_a_wrong_second_factor_code_is_audited(client: Client) -> None:
    user = UserFactory.create(staff=True)
    TOTPDeviceFactory.create(user=user)
    password_login(client, user)
    client.post(reverse("mfa:verify"), {"method": "totp", "token": "000000x"})
    entry = AuditLog.objects.get(action="accounts.mfa.verification_failed")
    assert entry.target_key == str(user.public_id)
    assert entry.after == {"method": "totp"}
    assert not AuditLog.objects.filter(action="accounts.mfa.verified").exists()
