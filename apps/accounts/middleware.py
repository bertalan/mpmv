"""MFA enforcement (CORE.4): no page, admin included, for an unverified required-role session.

Runs after `django_otp.middleware.OTPMiddleware`. A signed-in user who must use MFA (see
`services.mfa_required`), or who enrolled a factor voluntarily, and whose session is not
verified yet can only reach views marked `@mfa_exempt` (the MFA pages) and the sign-out of the
Django and Wagtail admins. Everything else redirects to enrolment (no confirmed factor yet) or
verification; JSON clients get 403.
"""

from collections.abc import Callable
from typing import Any

from django.http import HttpRequest
from django.http.response import HttpResponseBase
from django.utils import translation

from apps.core.i18n import configured_languages, translate_path

from . import levels
from .decorators import MFA_EXEMPT_ATTR, mfa_redirect
from .mfa_session import is_verified
from .services import has_second_factor, mfa_required

# Signing out is always possible: the staff admin's and the editors' (Wagtail, CMS.1).
EXEMPT_VIEW_NAMES = frozenset({"admin:logout", "wagtailadmin_logout"})


class SessionLevelMiddleware:
    """D-32 (CORE.25): end operational mode on idle or cap, and count the request as activity.

    After `MfaRequiredMiddleware`: a request that is turned away there is not activity.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponseBase]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        if request.user.is_authenticated:
            levels.refresh(request)
        return self.get_response(request)


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


class PreferredLanguageMiddleware:
    """I18N.12: a signed-in user's `preferred_language` decides the language of unprefixed areas.

    The language switcher sets no cookie (the URL prefix decides), so the Manager, the account
    area and the admins, which have no prefix, would stay in the primary language. Runs right after
    `AuthenticationMiddleware`. Public pages (routes in `i18n_patterns`) keep their prefix as the
    only source; anonymous users, an unknown or no longer configured language change nothing.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponseBase]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        user = getattr(request, "user", None)
        language = getattr(user, "preferred_language", "") if user is not None else ""
        if (
            user is not None
            and user.is_authenticated
            and language in configured_languages()
            and language != translation.get_language()
            and not self._is_public_page(request.path_info, language)
        ):
            translation.activate(language)
            request.LANGUAGE_CODE = language
        return self.get_response(request)

    @staticmethod
    def _is_public_page(path: str, language: str) -> bool:
        """True for a route of `i18n_patterns`, which exists in every language."""
        return translate_path(path, language) != path or (
            translation.get_language_from_path(path) is not None
        )
