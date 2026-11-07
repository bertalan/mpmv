"""CORE.4: authenticator-app (TOTP) enrolment and verification through the MFA pages."""

import re

import pytest
from django.test import Client
from django.urls import reverse
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts import engine
from apps.accounts.models import RecoveryCode, User
from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import password_login, totp_token, verified_login
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

CODE_RE = re.compile(r"\b[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}\b")


@pytest.fixture
def staff(client: Client) -> User:
    user = UserFactory.create(email="staff@example.com", staff=True)
    password_login(client, user)
    return user


def test_the_setup_page_offers_an_authenticator_app_and_a_passkey(
    client: Client, staff: User
) -> None:
    content = client.get(reverse("mfa:setup")).content.decode()
    assert reverse("mfa:totp") in content
    assert reverse("mfa:passkey_register_options") in content


def test_enrolment_shows_a_qr_code_and_the_secret_of_one_pending_device(
    client: Client, staff: User
) -> None:
    first = client.get(reverse("mfa:totp"))
    second = client.get(reverse("mfa:totp"))
    device = TOTPDevice.objects.get(user=staff)
    assert not device.confirmed
    secret = engine.totp_secret(device.bin_key)
    for response in (first, second):
        content = response.content.decode()
        assert "<svg" in content
        assert secret in content
    assert TOTPDevice.objects.filter(user=staff).count() == 1


def test_a_wrong_code_does_not_confirm_the_device(client: Client, staff: User) -> None:
    client.get(reverse("mfa:totp"))
    device = TOTPDevice.objects.get(user=staff)
    wrong = totp_token(device, offset_steps=10)
    response = client.post(reverse("mfa:totp"), {"token": wrong})
    assert response.status_code == 200
    assert "Invalid code" in response.content.decode()
    device.refresh_from_db()
    assert not device.confirmed
    assert client.get("/django-admin/").status_code == 302


def test_the_right_code_confirms_verifies_and_shows_recovery_codes_once(
    client: Client, staff: User
) -> None:
    client.get(reverse("mfa:totp") + "?next=/django-admin/")
    device = TOTPDevice.objects.get(user=staff)
    response = client.post(
        reverse("mfa:totp") + "?next=/django-admin/", {"token": totp_token(device)}
    )
    assert response.status_code == 200
    codes = CODE_RE.findall(response.content.decode())
    assert len(codes) == engine.RECOVERY_CODE_COUNT
    assert "/django-admin/" in response.content.decode()  # the continue link

    device.refresh_from_db()
    assert device.confirmed
    assert client.get("/django-admin/").status_code == 200

    stored = set(
        RecoveryCode.objects.filter(device__user=staff).values_list("code_hash", flat=True)
    )
    assert stored == {engine.hash_recovery_code(code) for code in codes}
    assert not stored & {code.replace("-", "") for code in codes}

    page = client.get(reverse("mfa:recovery_codes")).content.decode()
    assert not CODE_RE.findall(page)  # never shown again
    assert "10 unused" in page

    audit = AuditLog.objects.get(action="accounts.mfa.enrolled")
    assert audit.actor_id == staff.pk
    assert audit.target_model == "otp_totp.totpdevice"
    assert audit.after is not None
    assert audit.after["kind"] == "totp"
    assert AuditLog.objects.filter(action="accounts.mfa.recovery_codes_generated").count() == 1


def test_a_second_device_does_not_replace_the_recovery_codes(client: Client) -> None:
    user = UserFactory.create(staff=True)
    verified_login(client, user)
    client.post(reverse("mfa:recovery_codes"))
    before = set(RecoveryCode.objects.values_list("code_hash", flat=True))
    client.get(reverse("mfa:totp"))
    pending = TOTPDevice.objects.get(user=user, confirmed=False)
    response = client.post(reverse("mfa:totp"), {"token": totp_token(pending)})
    assert not CODE_RE.findall(response.content.decode())
    assert set(RecoveryCode.objects.values_list("code_hash", flat=True)) == before


def test_verification_with_the_current_code_opens_the_admin(client: Client) -> None:
    user = UserFactory.create(staff=True)
    device = TOTPDeviceFactory.create(user=user)
    password_login(client, user)
    response = client.post(
        reverse("mfa:verify") + "?next=/django-admin/",
        {"method": "totp", "token": totp_token(device)},
    )
    assert response.status_code == 302
    assert response["Location"] == "/django-admin/"
    assert client.get("/django-admin/").status_code == 200


def test_verification_with_a_wrong_code_keeps_the_admin_closed(client: Client) -> None:
    user = UserFactory.create(staff=True)
    device = TOTPDeviceFactory.create(user=user)
    password_login(client, user)
    response = client.post(
        reverse("mfa:verify"), {"method": "totp", "token": totp_token(device, offset_steps=10)}
    )
    assert response.status_code == 200
    assert "Invalid code" in response.content.decode()
    assert client.get("/django-admin/").status_code == 302


def test_a_code_cannot_be_replayed(client: Client) -> None:
    user = UserFactory.create(staff=True)
    device = TOTPDeviceFactory.create(user=user)
    token = totp_token(device)
    password_login(client, user)
    assert client.post(reverse("mfa:verify"), {"method": "totp", "token": token}).status_code == 302
    other = Client()
    password_login(other, user)
    assert other.post(reverse("mfa:verify"), {"method": "totp", "token": token}).status_code == 200
    assert other.get("/django-admin/").status_code == 302


def test_an_off_site_next_url_is_ignored(client: Client) -> None:
    user = UserFactory.create(staff=True)
    device = TOTPDeviceFactory.create(user=user)
    password_login(client, user)
    response = client.post(
        reverse("mfa:verify") + "?next=https://evil.example.net/",
        {"method": "totp", "token": totp_token(device)},
    )
    assert response["Location"] == "/"


def test_an_enrolled_user_cannot_add_a_device_with_the_password_only(client: Client) -> None:
    """Otherwise a stolen password would be enough to enrol the attacker's own authenticator."""
    user = UserFactory.create(staff=True)
    TOTPDeviceFactory.create(user=user)
    password_login(client, user)
    response = client.get(reverse("mfa:totp"))
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("mfa:verify"))
    assert TOTPDevice.objects.filter(user=user).count() == 1


def test_the_mfa_pages_require_a_signed_in_user(client: Client) -> None:
    for name in ("mfa:setup", "mfa:totp", "mfa:verify", "mfa:overview"):
        response = client.get(reverse(name))
        assert response.status_code == 302
        assert "/django-admin/login/" in response["Location"]
