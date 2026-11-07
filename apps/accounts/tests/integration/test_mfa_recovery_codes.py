"""CORE.4: recovery codes are stored hashed, single use, audited when used, regenerable."""

import re

import pytest
from django.test import Client
from django.urls import reverse
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.accounts import engine
from apps.accounts.models import RecoveryCode
from apps.accounts.tests.factories import (
    RecoveryCodeDeviceFactory,
    RecoveryCodeFactory,
    TOTPDeviceFactory,
    UserFactory,
)
from apps.accounts.tests.mfa import password_login, verified_login
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

CODE_RE = re.compile(r"\b[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}\b")


def _use(client: Client, code: str) -> int:
    response = client.post(reverse("mfa:verify"), {"method": "recovery", "code": code})
    return response.status_code


def test_a_recovery_code_verifies_once_and_is_audited(client: Client) -> None:
    user = UserFactory.create(staff=True)
    TOTPDeviceFactory.create(user=user)
    code = engine.generate_recovery_codes(1)[0]
    device = RecoveryCodeDeviceFactory.create(user=user)
    RecoveryCodeFactory.create(device=device, code=code)
    RecoveryCodeFactory.create(device=device)

    password_login(client, user)
    assert _use(client, code.upper().replace("-", " ")) == 302
    assert client.get("/django-admin/").status_code == 200

    audit = AuditLog.objects.get(action="accounts.mfa.recovery_code_used")
    assert audit.actor_id == user.pk
    assert audit.before == {"unused_codes": 2}
    assert audit.after == {"unused_codes": 1}

    again = Client()
    password_login(again, user)
    assert _use(again, code) == 200
    assert again.get("/django-admin/").status_code == 302
    assert AuditLog.objects.filter(action="accounts.mfa.recovery_code_used").count() == 1


def test_an_unknown_recovery_code_is_refused(client: Client) -> None:
    user = UserFactory.create(staff=True)
    TOTPDeviceFactory.create(user=user)
    RecoveryCodeFactory.create(device=RecoveryCodeDeviceFactory.create(user=user))
    password_login(client, user)
    response = client.post(
        reverse("mfa:verify"),
        {"method": "recovery", "code": engine.generate_recovery_codes(1)[0]},
    )
    assert "Invalid code" in response.content.decode()
    assert client.get("/django-admin/").status_code == 302


def test_another_users_recovery_code_is_refused(client: Client) -> None:
    code = engine.generate_recovery_codes(1)[0]
    RecoveryCodeFactory.create(code=code)  # belongs to someone else
    user = UserFactory.create(staff=True)
    TOTPDeviceFactory.create(user=user)
    RecoveryCodeDeviceFactory.create(user=user)
    password_login(client, user)
    assert _use(client, code) == 200


def test_regenerating_replaces_every_code_and_shows_the_new_ones_once(
    client: Client, settings: SettingsWrapper
) -> None:
    settings.MFA_RECOVERY_THROTTLE_FACTOR = 0  # two attempts in a row below
    user = UserFactory.create(staff=True)
    old = engine.generate_recovery_codes(1)[0]
    RecoveryCodeFactory.create(device=RecoveryCodeDeviceFactory.create(user=user), code=old)
    verified_login(client, user)

    response = client.post(reverse("mfa:recovery_codes"))
    assert response.status_code == 200
    new_codes = CODE_RE.findall(response.content.decode())
    assert len(new_codes) == engine.RECOVERY_CODE_COUNT
    assert old not in new_codes
    assert set(RecoveryCode.objects.values_list("code_hash", flat=True)) == {
        engine.hash_recovery_code(code) for code in new_codes
    }
    assert AuditLog.objects.filter(action="accounts.mfa.recovery_codes_generated").count() == 1

    other = Client()
    password_login(other, user)
    assert _use(other, old) == 200
    assert _use(other, new_codes[0]) == 302


def test_regenerating_requires_a_recent_verification(client: Client) -> None:
    user = UserFactory.create(staff=True)
    verified_login(client, user, verified_at=0.0)  # verified long ago
    response = client.post(reverse("mfa:recovery_codes"))
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("mfa:verify"))
    assert not RecoveryCode.objects.exists()


def test_failed_attempts_are_throttled(client: Client) -> None:
    user = UserFactory.create(staff=True)
    TOTPDeviceFactory.create(user=user)
    code = engine.generate_recovery_codes(1)[0]
    RecoveryCodeFactory.create(device=RecoveryCodeDeviceFactory.create(user=user), code=code)
    password_login(client, user)
    assert _use(client, engine.generate_recovery_codes(1)[0]) == 200
    assert _use(client, code) == 200  # right code, but too soon after a failure
    assert RecoveryCode.objects.get().used_at is None
