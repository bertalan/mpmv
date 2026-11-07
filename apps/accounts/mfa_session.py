"""Second-factor state of the current session (CORE.4).

django-otp's `OTPMiddleware` remembers which device verified the session; we also store when,
so sensitive views can ask for a recent verification (step-up).
"""

import time

import django_otp
from django.conf import settings
from django.http import HttpRequest

from . import engine

VERIFIED_AT_SESSION_KEY = "mfa_verified_at"


def complete_verification(request: HttpRequest, device: object) -> None:
    """Mark the session as verified by `device` (which must belong to `request.user`), now.

    The session key is rotated, as on any privilege change, so a fixed session id from before
    the verification is worthless.
    """
    request.session.cycle_key()
    django_otp.login(request, device)
    request.session[VERIFIED_AT_SESSION_KEY] = time.time()


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
