"""Signal receivers that count and audit login attempts (CORE.5, SEC.4). Connected in
`AccountsConfig.ready`."""

from typing import Any

from django.contrib.auth.base_user import AbstractBaseUser
from django.http import HttpRequest

from apps.core.http import client_ip

from .http import user_context
from .models import User
from .services import record_failed_login, record_successful_login


def count_failed_login(
    sender: Any, credentials: dict[str, Any], request: HttpRequest | None = None, **kwargs: Any
) -> None:
    identifier = credentials.get("username", credentials.get(User.USERNAME_FIELD))
    ip = client_ip(request) if request is not None else None
    record_failed_login(identifier if isinstance(identifier, str) else None, ip)


def reset_after_login(
    sender: Any, request: HttpRequest | None, user: AbstractBaseUser, **kwargs: Any
) -> None:
    if not isinstance(user, User):
        return
    ip = client_ip(request) if request is not None else None
    record_successful_login(user, ctx=user_context(user, ip))
