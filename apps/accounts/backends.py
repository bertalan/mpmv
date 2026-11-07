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
