"""MFA enforcement (CORE.4): no page, admin included, for an unverified required-role session.

Runs after `django_otp.middleware.OTPMiddleware`. A signed-in user who must use MFA (see
`services.mfa_required`), or who enrolled a factor voluntarily, and whose session is not
verified yet can only reach views marked `@mfa_exempt` (the MFA pages) and the sign-out of the
Django and Wagtail admins. Everything else redirects to enrolment (no confirmed factor yet) or
verification; JSON clients get 403.
"""

from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode

from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest, HttpResponseRedirect, JsonResponse
from django.http.response import HttpResponseBase
from django.urls import reverse
from django.utils import translation

from apps.core.http import client_ip
from apps.core.i18n import configured_languages, translate_path

from . import levels, lifetimes, services
from .decorators import MFA_EXEMPT_ATTR, mfa_redirect
from .http import return_url, wants_json
from .mfa_session import is_verified
from .models import User
from .services import has_second_factor, mfa_required
from .session_language import SESSION_LANGUAGE_KEY
from .session_store import generation_matches, touch_session

# Signing out is always possible: the staff admin's and the editors' (Wagtail, CMS.1).
EXEMPT_VIEW_NAMES = frozenset({"admin:logout", "wagtailadmin_logout"})


class SessionLevelMiddleware:
    """D-32 (CORE.25): end the passkey window on idle or cap, and count the request as activity.

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


# CORE.28: what a user who must change a breached password may still reach: the change page,
# the second-factor pages a sign-in needs (not the factor list or removal), sign-out, and what
# the change page loads (images, the palette stylesheet).
BREACH_ALLOWED_VIEW_NAMES = frozenset(
    {
        "password:change",
        "mfa:setup",
        "mfa:totp",
        "mfa:verify",
        "mfa:recovery_codes",
        "mfa:passkey_register_options",
        "mfa:passkey_register",
        "mfa:passkey_auth_options",
        "mfa:passkey_auth",
        "logout",
        "admin:logout",
        "wagtailadmin_logout",
        "media",
        "palette:css",
        "healthz",
        "readyz",
    }
)


class BreachedPasswordMiddleware:
    """CORE.28: a sign-in found the password in a known breach (`User.password_breached_at`).

    After `MfaRequiredMiddleware`, so a role that must use MFA verifies the second factor first.
    Every other page then redirects to the change page (JSON clients get 403) until a new
    password is set, which clears the flag. The account's activity is not blocked otherwise.
    """

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
        if not isinstance(user, User) or user.password_breached_at is None:
            return None
        match = request.resolver_match
        if match is not None and match.view_name in BREACH_ALLOWED_VIEW_NAMES:
            return None
        url = reverse("password:change")
        if wants_json(request):
            return JsonResponse({"error": "password_change_required", "url": url}, status=403)
        return HttpResponseRedirect(f"{url}?{urlencode({'next': return_url(request)})}")


class PreferredLanguageMiddleware:
    """I18N.12: a signed-in user's `preferred_language` decides the language of unprefixed areas.

    The language switcher sets no cookie (the URL prefix decides), so the Manager, the account
    area and the admins, which have no prefix, would stay in the primary language. Runs right after
    `AuthenticationMiddleware`. Public pages (routes in `i18n_patterns`) keep their prefix as the
    only source. An anonymous visitor's session choice (`mpmv_language`, I18N.11, no cookie) is read
    after the stored preference; an unknown or no longer configured language changes nothing.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponseBase]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        language = self._language(request)
        if (
            language in configured_languages()
            and language != translation.get_language()
            and not self._is_public_page(request.path_info, language)
        ):
            translation.activate(language)
            request.LANGUAGE_CODE = language
        return self.get_response(request)

    @staticmethod
    def _language(request: HttpRequest) -> str:
        """A signed-in user's stored preference, else the session's choice (I18N.11), else ''."""
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            return str(getattr(user, "preferred_language", ""))
        session = getattr(request, "session", None)
        return str(session.get(SESSION_LANGUAGE_KEY, "")) if session is not None else ""

    @staticmethod
    def _is_public_page(path: str, language: str) -> bool:
        """True for a route of `i18n_patterns`, which exists in every language."""
        return translate_path(path, language) != path or (
            translation.get_language_from_path(path) is not None
        )


class SessionIndexMiddleware:
    """CORE.24: keep the session index fresh and name the request's session to the services.

    A session whose copy of the user's session generation is old was ended (also when the
    index never saw it): it is flushed and the request is anonymous. A session whose role-based
    lifetime is over (`lifetimes.enforce`, CORE.24 part 2) is ended, audited and sent to the
    login. Otherwise the request
    refreshes its `UserSession` row at most every `SESSION_TOUCH_SECONDS` (at once when the row
    is missing), never the session data, so the session's expiry does not move. The request's
    session is bound for `services.end_sessions_after_change`, so a change a user makes to their
    own account keeps the session they made it from.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponseBase]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        session = getattr(request, "session", None)
        user = getattr(request, "user", None)
        if session is not None and isinstance(user, User) and user.is_authenticated:
            if not generation_matches(user, session):
                # This user's sessions were ended after this one signed in (CORE.24).
                session.flush()
                request.user = AnonymousUser()
            elif (ended := lifetimes.enforce(request)) is not None:
                return ended  # the idle or absolute lifetime is over (CORE.24 part 2)
            else:
                touch_session(
                    user,
                    session,
                    user_agent=request.headers.get("user-agent"),
                    ip=client_ip(request),
                )
        token = services.bind_current_session(session)
        try:
            return self.get_response(request)
        finally:
            services.unbind_current_session(token)
