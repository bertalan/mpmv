"""CORE.4: removing factors, resetting MFA (disable) and the admin action; dry-run and audit."""

import pytest
from django.test import Client
from django.urls import reverse
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts import services
from apps.accounts.models import PasskeyDevice, RecoveryCode, RecoveryCodeDevice, User
from apps.accounts.tests.factories import (
    PasskeyDeviceFactory,
    RecoveryCodeDeviceFactory,
    RecoveryCodeFactory,
    TOTPDeviceFactory,
    UserFactory,
)
from apps.accounts.tests.mfa import password_login, verified_login
from apps.core.audit import AuditContext
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="staff:admin@example.com", source="admin", actor_id=1)


def _enrolled_staff() -> User:
    user = UserFactory.create(staff=True)
    TOTPDeviceFactory.create(user=user)
    PasskeyDeviceFactory.create(user=user)
    RecoveryCodeFactory.create(device=RecoveryCodeDeviceFactory.create(user=user))
    return user


def test_has_second_factor_ignores_recovery_codes_and_unconfirmed_devices() -> None:
    user = UserFactory.create(staff=True)
    RecoveryCodeDeviceFactory.create(user=user)
    TOTPDeviceFactory.create(user=user, confirmed=False)
    assert services.has_second_factor(user) is False
    PasskeyDeviceFactory.create(user=user)
    assert services.has_second_factor(user) is True


def test_removing_one_of_two_factors_is_audited() -> None:
    user = _enrolled_staff()
    passkey = PasskeyDevice.objects.get(user=user)
    changeset = services.remove_device(passkey, ctx=CTX)
    assert changeset.summary() == {"create": 0, "update": 0, "delete": 1}
    assert not PasskeyDevice.objects.filter(user=user).exists()
    audit = AuditLog.objects.get(action="accounts.mfa.device_removed")
    assert audit.target_model == "accounts.passkeydevice"
    assert audit.target_key == str(passkey.public_id)


def test_a_required_role_cannot_remove_its_last_factor() -> None:
    user = UserFactory.create(staff=True)
    device = TOTPDeviceFactory.create(user=user)
    with pytest.raises(services.MfaPolicyError):
        services.remove_device(device, ctx=CTX)
    assert TOTPDevice.objects.filter(pk=device.pk).exists()
    assert not AuditLog.objects.exists()


def test_a_customer_removing_the_last_factor_disables_mfa() -> None:
    user = UserFactory.create()
    device = TOTPDeviceFactory.create(user=user)
    RecoveryCodeFactory.create(device=RecoveryCodeDeviceFactory.create(user=user))
    services.remove_device(device, ctx=CTX)
    assert not RecoveryCodeDevice.objects.filter(user=user).exists()
    assert set(AuditLog.objects.values_list("action", flat=True)) == {
        "accounts.mfa.device_removed",
        "accounts.mfa.disabled",
    }


def test_remove_device_dry_run_changes_nothing() -> None:
    user = _enrolled_staff()
    changeset = services.remove_device(PasskeyDevice.objects.get(user=user), ctx=CTX, dry_run=True)
    assert not changeset.is_empty
    assert PasskeyDevice.objects.filter(user=user).exists()
    assert not AuditLog.objects.exists()


def test_reset_mfa_deletes_every_factor_and_forces_a_new_enrolment(client: Client) -> None:
    user = _enrolled_staff()
    verified_login(client, user, TOTPDevice.objects.get(user=user))
    changeset = services.reset_mfa(user, ctx=CTX)
    assert changeset.summary()["delete"] == 3
    assert not TOTPDevice.objects.filter(user=user).exists()
    assert not PasskeyDevice.objects.filter(user=user).exists()
    assert not RecoveryCode.objects.filter(device__user=user).exists()
    audit = AuditLog.objects.get(action="accounts.mfa.disabled")
    assert audit.actor_label == CTX.actor_label
    assert audit.before is not None
    assert audit.before["factors"] == ["passkey", "recovery_codes", "totp"]
    # The open session was verified by a device that no longer exists.
    assert client.get("/django-admin/")["Location"].startswith(reverse("mfa:setup"))


def test_reset_mfa_dry_run_changes_nothing() -> None:
    user = _enrolled_staff()
    assert services.reset_mfa(user, ctx=CTX, dry_run=True).summary()["delete"] == 3
    assert TOTPDevice.objects.filter(user=user).exists()
    assert not AuditLog.objects.exists()


def test_the_overview_lists_factors_and_removes_one_with_step_up(client: Client) -> None:
    user = _enrolled_staff()
    verified_login(client, user, TOTPDevice.objects.get(user=user))
    passkey = PasskeyDevice.objects.get(user=user)
    page = client.get(reverse("mfa:overview")).content.decode()
    assert passkey.name in page
    url = reverse("mfa:remove_device", kwargs={"kind": "passkey", "public_id": passkey.public_id})
    assert url in page
    assert client.post(url)["Location"] == reverse("mfa:overview")
    assert not PasskeyDevice.objects.filter(pk=passkey.pk).exists()


def test_removing_a_factor_needs_a_recent_verification(client: Client) -> None:
    user = _enrolled_staff()
    verified_login(client, user, TOTPDevice.objects.get(user=user), verified_at=0.0)
    passkey = PasskeyDevice.objects.get(user=user)
    url = reverse("mfa:remove_device", kwargs={"kind": "passkey", "public_id": passkey.public_id})
    assert client.post(url)["Location"].startswith(reverse("mfa:verify"))
    assert PasskeyDevice.objects.filter(pk=passkey.pk).exists()


def test_a_user_cannot_remove_another_users_factor(client: Client) -> None:
    verified_login(client, UserFactory.create(staff=True))
    other = PasskeyDeviceFactory.create()
    url = reverse("mfa:remove_device", kwargs={"kind": "passkey", "public_id": other.public_id})
    assert client.post(url).status_code == 404
    assert PasskeyDevice.objects.filter(pk=other.pk).exists()


def test_staff_reset_mfa_from_the_admin(client: Client) -> None:
    admin_user = UserFactory.create(superuser=True)
    verified_login(client, admin_user)
    target = _enrolled_staff()
    response = client.post(
        reverse("admin:accounts_user_changelist"),
        {"action": "reset_mfa", "_selected_action": [target.pk]},
    )
    assert response.status_code == 302
    assert not services.has_second_factor(target)
    audit = AuditLog.objects.get(action="accounts.mfa.disabled")
    assert audit.actor_id == admin_user.pk
    assert audit.source == "admin"


def test_the_admin_reset_needs_a_recent_verification(client: Client) -> None:
    admin_user = UserFactory.create(superuser=True)
    verified_login(client, admin_user, verified_at=0.0)
    target = _enrolled_staff()
    client.post(
        reverse("admin:accounts_user_changelist"),
        {"action": "reset_mfa", "_selected_action": [target.pk]},
    )
    assert services.has_second_factor(target)
    assert not AuditLog.objects.filter(action__startswith="accounts.mfa.").exists()


def test_devices_cannot_be_created_from_the_admin(client: Client) -> None:
    """Enrolment goes through the audited MFA pages only, never through raw admin forms."""
    verified_login(client, UserFactory.create(superuser=True))
    assert client.get("/django-admin/otp_totp/totpdevice/").status_code == 404


def test_password_only_session_cannot_open_the_overview_of_a_required_role(
    client: Client,
) -> None:
    user = _enrolled_staff()
    password_login(client, user)
    response = client.get(reverse("mfa:overview"))
    assert response["Location"].startswith(reverse("mfa:verify"))
