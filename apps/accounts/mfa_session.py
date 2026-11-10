"""Second-factor state of the current session (CORE.4).

django-otp's `OTPMiddleware` remembers which device verified the session; we also store when,
so sensitive views can ask for a recent verification (step-up).
"""

import time

import django_otp
from django.conf import settings
from django.http import HttpRequest

from apps.core.audit import audit_context

from . import engine, services

VERIFIED_AT_SESSION_KEY = "mfa_verified_at"
VERIFIED_KIND_SESSION_KEY = "mfa_verified_kind"  # the factor kind of the last verification


def complete_verification(request: HttpRequest, device: object) -> None:
    """Mark the session as verified by `device` (which must belong to `request.user`), now.

    The session key is rotated, as on any privilege change, so a fixed session id from before
    the verification is worthless.
    """
    request.session.cycle_key()
    django_otp.login(request, device)
    request.session[VERIFIED_AT_SESSION_KEY] = time.time()
    request.session[VERIFIED_KIND_SESSION_KEY] = services.kind_of(device)
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
    """Like `is_recently_verified`, but the last verification must have been a passkey (D-32
    group 3: a code or a recovery code does not count)."""
    return (
        is_recently_verified(request, max_age)
        and request.session.get(VERIFIED_KIND_SESSION_KEY) == services.PASSKEY
    )
