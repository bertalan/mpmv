"""The cart of the current request, and the merge on sign-in (ORD.1). A thin adapter over
`services.py`: views and the checkout (ORD.5) call `current_cart`.

A guest cart is keyed by the session key it was created in, and the session data remembers
that key. Django changes the session key on sign-in (`cycle_key`) but keeps the data, so the
`user_logged_in` receiver still finds the guest cart and merges it into the user's cart.
"""

from typing import Any

from django.contrib.auth.base_user import AbstractBaseUser
from django.http import HttpRequest

from apps.accounts.models import User

from . import services
from .models import Cart

GUEST_CART_SESSION_KEY = "cart.guest_session_key"


def current_cart(request: HttpRequest, *, create: bool = False) -> Cart | None:
    """The signed-in user's cart, or the visitor's guest cart; created only when `create`."""
    user = request.user
    if user.is_authenticated and isinstance(user, User):
        if create:
            return services.get_or_create_cart(user=user, language=_language(request))
        return services.get_cart(user=user)
    key = request.session.get(GUEST_CART_SESSION_KEY)
    if not isinstance(key, str) or not key:
        if not create:
            return None
        if request.session.session_key is None:
            request.session.save()  # gives the new session its key
        key = request.session.session_key
        if key is None:
            raise RuntimeError("The session has no key after being saved")
        request.session[GUEST_CART_SESSION_KEY] = key
    if create:
        return services.get_or_create_cart(session_key=key, language=_language(request))
    return services.get_cart(session_key=key)


def _language(request: HttpRequest) -> str | None:
    language = getattr(request, "LANGUAGE_CODE", None)
    return language if isinstance(language, str) else None


def merge_guest_cart_on_login(
    sender: Any, request: HttpRequest | None, user: AbstractBaseUser, **kwargs: Any
) -> None:
    """`user_logged_in` receiver: merge the visitor's guest cart into the user's cart."""
    if request is None or not isinstance(user, User) or not hasattr(request, "session"):
        return
    key = request.session.pop(GUEST_CART_SESSION_KEY, None)
    if not isinstance(key, str) or not key:
        return
    guest = services.get_cart(session_key=key)
    if guest is not None:
        services.merge_carts(guest, user)
