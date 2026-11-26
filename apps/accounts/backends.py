"""Authentication backends (CORE.5).

`LoginProtectionBackend` is listed first in `AUTHENTICATION_BACKENDS`, so every
`django.contrib.auth.authenticate()` call (Django admin, future customer and vendor logins, MFA
flows) passes through it before any password is checked.
"""

from typing import Any

from django.contrib.auth.backends import BaseBackend, ModelBackend
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest

from apps.core.http import client_ip

from .breach import password_is_breached
from .captcha import captcha_passes
from .models import User
from .services import login_retry_after


class LoginProtectionBackend(BaseBackend):
    """Refuse an attempt while its account or IP is locked out, or without a valid captcha.

    It never authenticates anyone. Raising PermissionDenied stops `authenticate()` before the
    other backends run, and the login form shows its usual generic error: a locked account,
    an unknown email and a wrong password look the same. The `user_login_failed` signal that
    follows is counted by `apps.accounts.signals`.
    """

    def authenticate(
        self,
        request: HttpRequest | None,
        username: str | None = None,
        password: str | None = None,
        **kwargs: Any,
    ) -> None:
        identifier = username if username is not None else kwargs.get(User.USERNAME_FIELD)
        ip = client_ip(request) if request is not None else None
        if login_retry_after(identifier, ip):
            raise PermissionDenied
        if request is not None and not captcha_passes(request):
            raise PermissionDenied
        return None

    def get_user(self, user_id: int) -> User | None:
        """Resolve sessions that name this backend like `ModelBackend` does.

        `login()` records the backend that authenticated the user, never this one; but
        `Client.force_login()` and `login()` without `backend=` fall back to the first entry
        of `AUTHENTICATION_BACKENDS`, which is this one.
        """
        user = ModelBackend().get_user(user_id)
        return user if isinstance(user, User) else None


# Set on the user object `authenticate()` returns when its password is in a known breach;
# `signals.flag_breached_on_login` records it once the sign-in completes (CORE.28).
BREACH_FOUND_ATTR = "_mpmv_password_breached"


class BreachCheckBackend(ModelBackend):
    """`ModelBackend`, plus the breached-password check of each successful password sign-in.

    Listed right before `ModelBackend` (kept for existing sessions and explicit `backend=`
    logins). The check fails open (`password_is_breached`): the service down lets the sign-in
    through and the check runs again next time. An account already flagged is not checked again.
    A wrong password raises PermissionDenied, so `ModelBackend` does not hash it a second time;
    `authenticate()` then answers None and sends `user_login_failed`, as when every backend fails.
    """

    def authenticate(
        self,
        request: HttpRequest | None,
        username: str | None = None,
        password: str | None = None,
        **kwargs: Any,
    ) -> User | None:
        if password is None or (username is None and kwargs.get(User.USERNAME_FIELD) is None):
            return None  # not a password sign-in: let the other backends decide
        user = super().authenticate(request, username=username, password=password, **kwargs)
        if user is None:
            raise PermissionDenied
        if isinstance(user, User) and user.password_breached_at is None:
            setattr(user, BREACH_FOUND_ATTR, password_is_breached(password))
        return user
