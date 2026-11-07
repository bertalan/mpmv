"""Test helpers for MFA (CORE.4): current TOTP codes and logins with a verified second factor.

Staff and superusers cannot use the site or the admin until they verify a second factor, so
tests that act as staff log in through `verified_login` (or the root `mfa_login` fixture).
"""

import time

from django.test import Client
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.oath import TOTP
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts.mfa_session import VERIFIED_AT_SESSION_KEY
from apps.accounts.models import User
from apps.accounts.tests.factories import TOTPDeviceFactory


def totp_token(device: TOTPDevice, *, offset_steps: int = 0) -> str:
    """The code an authenticator app shows now (or `offset_steps` steps away) for `device`."""
    totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
    totp.time = time.time() + offset_steps * device.step
    return str(totp.token()).zfill(device.digits)


def verified_login(
    client: Client,
    user: User,
    device: TOTPDevice | None = None,
    *,
    verified_at: float | None = None,
) -> TOTPDevice:
    """Log `user` in with a second factor already verified (a confirmed TOTP device)."""
    device = device or TOTPDeviceFactory.create(user=user)
    client.force_login(user)
    session = client.session
    session[DEVICE_ID_SESSION_KEY] = device.persistent_id
    session[VERIFIED_AT_SESSION_KEY] = time.time() if verified_at is None else verified_at
    session.save()
    return device


def password_login(client: Client, user: User) -> None:
    """Log in with the password only: authenticated, second factor not verified yet."""
    client.force_login(user)
