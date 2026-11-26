"""Second-factor state of the current session (CORE.4).

django-otp's `OTPMiddleware` remembers which device verified the session; we also store when,
so sensitive views can ask for a recent verification (step-up).
"""

import time

import django_otp
from django.conf import settings
from django.http import HttpRequest
from django_otp import DEVICE_ID_SESSION_KEY

from apps.core.audit import audit_context

from . import engine, services

VERIFIED_AT_SESSION_KEY = "mfa_verified_at"
VERIFIED_KIND_SESSION_KEY = "mfa_verified_kind"  # the factor kind of the last verification
VERIFIED_UV_SESSION_KEY = (
    "mfa_verified_uv"  # that verification was a passkey with user verification
)


def complete_verification(
    request: HttpRequest, device: object, *, user_verified: bool = False
) -> None:
    """Mark the session as verified by `device` (which must belong to `request.user`), now.

    The session key is rotated, as on any privilege change, so a fixed session id from before
    the verification is worthless.
    """
    request.session.cycle_key()
    django_otp.login(request, device)
    request.session[VERIFIED_AT_SESSION_KEY] = time.time()
    request.session[VERIFIED_KIND_SESSION_KEY] = services.kind_of(device)
    request.session[VERIFIED_UV_SESSION_KEY] = user_verified
    services.record_mfa_verified(device, ctx=audit_context(request))


def is_verified(request: HttpRequest) -> bool:
    user = request.user
    check = getattr(user, "is_verified", None)
    return bool(user.is_authenticated and callable(check) and check())


def is_recently_verified(request: HttpRequest, max_age: int | None = None) -> bool:
    """Verified with a second factor at most `max_age` seconds ago (default: settings)."""
    if not is_verified(request):
        return False
    verified_at = request.session.get(VERIFIED_AT_SESSION_KEY)
    limit = settings.MFA_STEP_UP_MAX_AGE if max_age is None else max_age
    return engine.is_fresh(
        float(verified_at) if isinstance(verified_at, int | float) else None,
        now=time.time(),
        max_age=limit,
    )


def is_recently_passkey_verified(request: HttpRequest, max_age: int | None = None) -> bool:
    """Like `is_recently_verified`, but the last verification must be a passkey checked with user
    verification (D-32 critical: a code, a recovery code or a plain login passkey is not enough)."""
    return (
        is_recently_verified(request, max_age)
        and request.session.get(VERIFIED_KIND_SESSION_KEY) == services.PASSKEY
        and request.session.get(VERIFIED_UV_SESSION_KEY) is True
    )


def drop_verification(request: HttpRequest) -> None:
    """Forget the session's second-factor verification: the next page asks for it again.

    CORE.28: after a vendor member replaces a breached password, a fresh authentication comes
    before any other action; `MfaRequiredMiddleware` sends the user to the verify page.
    """
    for key in (
        DEVICE_ID_SESSION_KEY,
        VERIFIED_AT_SESSION_KEY,
        VERIFIED_KIND_SESSION_KEY,
        VERIFIED_UV_SESSION_KEY,
    ):
        request.session.pop(key, None)
    request.user.otp_device = None  # type: ignore[union-attr]  # set by OTPMiddleware
