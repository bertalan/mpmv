"""MFA enforcement (CORE.4): no page, admin included, for an unverified required-role session.

Runs after `django_otp.middleware.OTPMiddleware`. A signed-in user who must use MFA (see
`services.mfa_required`), or who enrolled a factor voluntarily, and whose session is not
verified yet can only reach views marked `@mfa_exempt` (the MFA pages) and the admin logout.
Everything else redirects to enrolment (no confirmed factor yet) or verification; JSON
clients get 403.
"""

from collections.abc import Callable
from typing import Any

from django.http import HttpRequest
from django.http.response import HttpResponseBase

from .decorators import MFA_EXEMPT_ATTR, mfa_redirect
from .mfa_session import is_verified
from .services import has_second_factor, mfa_required

EXEMPT_VIEW_NAMES = frozenset({"admin:logout"})


class MfaRequiredMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponseBase]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        return self.get_response(request)

    def process_view(
        self,
        request: HttpRequest,
        view_func: Callable[..., Any],
        view_args: tuple[Any, ...],
        view_kwargs: dict[str, Any],
    ) -> HttpResponseBase | None:
        user = request.user
        if not user.is_authenticated or is_verified(request):
            return None
        if getattr(view_func, MFA_EXEMPT_ATTR, False):
            return None
        match = request.resolver_match
        if match is not None and match.view_name in EXEMPT_VIEW_NAMES:
            return None
        enrolled = has_second_factor(user)
        if not (enrolled or mfa_required(user)):
            return None
        return mfa_redirect(request, "mfa:verify" if enrolled else "mfa:setup")
