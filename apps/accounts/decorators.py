"""View decorators for account flows: registration protection (CORE.5), MFA exemption and
step-up (CORE.4)."""

import functools
import math
from collections.abc import Callable
from typing import Any, Concatenate
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseRedirect,
    JsonResponse,
)
from django.shortcuts import render, resolve_url
from django.urls import reverse
from django.utils.translation import gettext, gettext_noop

from apps.core import session_policy
from apps.core.http import client_ip

from . import levels
from .captcha import captcha_passes
from .http import wants_json
from .mfa_session import is_recently_passkey_verified, is_recently_verified
from .services import has_second_factor, record_registration_attempt, registration_retry_after

TOO_MANY_ATTEMPTS = gettext_noop("Too many attempts. Please try again later.")
VERIFICATION_FAILED = gettext_noop("The verification failed. Please reload the page and try again.")


def protect_registration[**P](
    view: Callable[Concatenate[HttpRequest, P], HttpResponse],
) -> Callable[Concatenate[HttpRequest, P], HttpResponse]:
    """Rate limit registration POSTs per IP (429 + Retry-After) and require the captcha.

    GET requests (showing the form) are never limited. Messages are generic: they say
    nothing about which accounts exist.
    """

    @functools.wraps(view)
    def wrapped(request: HttpRequest, /, *args: P.args, **kwargs: P.kwargs) -> HttpResponse:
        if request.method != "POST":
            return view(request, *args, **kwargs)
        ip = client_ip(request)
        wait = registration_retry_after(ip)
        if wait:
            return HttpResponse(
                gettext(TOO_MANY_ATTEMPTS),
                status=429,
                headers={"Retry-After": str(math.ceil(wait.total_seconds()))},
            )
        record_registration_attempt(ip)
        if not captcha_passes(request):
            return HttpResponseBadRequest(gettext(VERIFICATION_FAILED))
        return view(request, *args, **kwargs)

    return wrapped


MFA_EXEMPT_ATTR = "mfa_exempt"

type View[**P] = Callable[Concatenate[HttpRequest, P], HttpResponse]


def mfa_exempt[**P](view: View[P]) -> View[P]:
    """Let a signed-in user reach this view before verifying the second factor."""
    setattr(view, MFA_EXEMPT_ATTR, True)
    return view


def mfa_redirect(request: HttpRequest, name: str, next_url: str | None = None) -> HttpResponse:
    """Send the user to an MFA page (or answer 403 to JSON clients), keeping `next`."""
    if wants_json(request):
        return JsonResponse({"error": "mfa_required", "url": reverse(name)}, status=403)
    query = urlencode({"next": next_url if next_url is not None else request.get_full_path()})
    return HttpResponseRedirect(f"{reverse(name)}?{query}")


def step_up_required[**P](max_age: int | None = None) -> Callable[[View[P]], View[P]]:
    """Require a second-factor verification at most `max_age` seconds old.

    The default comes from `settings.MFA_STEP_UP_MAX_AGE`. Use it on sensitive views (IBAN,
    payouts, rates, data exports; spec 02 §2.2). Users without a factor are sent to enrolment.
    """

    def decorator(view: View[P]) -> View[P]:
        @functools.wraps(view)
        def wrapped(request: HttpRequest, /, *args: P.args, **kwargs: P.kwargs) -> HttpResponse:
            user = request.user
            if not user.is_authenticated:
                return redirect_to_login(
                    request.get_full_path(), resolve_url(settings.MFA_LOGIN_URL)
                )
            if not is_recently_verified(request, max_age):
                target = "mfa:verify" if has_second_factor(user) else "mfa:setup"
                return mfa_redirect(request, target)
            return view(request, *args, **kwargs)

        return wrapped

    return decorator


def _prompt(request: HttpRequest, action: str, group: int, digest: str) -> HttpResponse:
    """The refusal of a sensitive action in restricted mode, with the way forward (D-32)."""
    outcome = "restricted"
    levels.record_prompt(request, action, group, outcome)
    can_elevate = levels.may_elevate(request.user)
    if wants_json(request):
        return JsonResponse(
            {"error": "operational_mode_required", "action": action, "digest": digest}, status=403
        )
    replay = [
        (key, value)
        for key in request.POST
        if key != "csrfmiddlewaretoken"
        for value in request.POST.getlist(key)
    ]
    return render(
        request,
        "accounts/levels/prompt.html",
        {
            "action": action,
            "group": group,
            "digest": digest,
            "can_elevate": can_elevate,
            "can_do_once": can_elevate and group == session_policy.GROUP_OPERATIONAL,
            "has_passkey": levels.has_passkey(request.user),
            "replay_method": request.method,
            "replay_url": request.get_full_path(),
            "replay_fields": replay,
        },
        status=403,
    )


def requires_level[**P](action: str) -> Callable[[View[P]], View[P]]:
    """Gate a view by the session level its `action` needs (D-32, CORE.25).

    The group of `action` comes from the `PlatformOperator` configuration, per request:

    1. free: runs in any session;
    2. operational mode required: in restricted mode the user gets a prompt with "Do it once"
       (a fresh passkey authorises this action with these parameters, once, about 2 minutes) or
       "Switch to operational mode";
    3. operational mode and, per action, a passkey verification no older than
       `MFA_STEP_UP_MAX_AGE` (a code does not count), even inside operational mode.

    Stack it with `step_up_required` where the view must also confirm a recent second factor.
    """
    levels.REGISTERED_ACTIONS.add(action)

    def decorator(view: View[P]) -> View[P]:
        @functools.wraps(view)
        def wrapped(request: HttpRequest, /, *args: P.args, **kwargs: P.kwargs) -> HttpResponse:
            if not request.user.is_authenticated:
                return redirect_to_login(
                    request.get_full_path(), resolve_url(settings.MFA_LOGIN_URL)
                )
            group = levels.policy().group_of(action)
            if group == session_policy.GROUP_FREE:
                return view(request, *args, **kwargs)
            digest = levels.params_digest(request)
            if levels.current_level(request) != levels.OPERATIONAL:
                if group == session_policy.GROUP_OPERATIONAL and levels.consume_single(
                    request, action, digest
                ):
                    return view(request, *args, **kwargs)
                return _prompt(request, action, group, digest)
            if group == session_policy.GROUP_PASSKEY and not is_recently_passkey_verified(request):
                levels.record_prompt(request, action, group, "passkey_required")
                if wants_json(request):
                    return JsonResponse({"error": "passkey_required", "action": action}, status=403)
                query = urlencode({"next": request.get_full_path(), "factor": "passkey"})
                return HttpResponseRedirect(f"{reverse('mfa:verify')}?{query}")
            return view(request, *args, **kwargs)

        return wrapped

    return decorator


_ACTION_PASSED = HttpResponse()


def _action_passed(request: HttpRequest) -> HttpResponse:
    return _ACTION_PASSED


def level_gated_action[**A](action: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Gate a Django admin action (`modeladmin, request, queryset`) by `requires_level(action)`.

    The check runs on the request when the action runs, whatever `action` values the POST
    carries; when the level is not met the action's response (the prompt or the redirect) is
    returned and nothing changes.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        def guarded(modeladmin: Any, request: HttpRequest, queryset: Any) -> Any:
            refused = requires_level(action)(_action_passed)(request)
            if refused is not _ACTION_PASSED:
                return refused
            return func(modeladmin, request, queryset)

        return guarded

    return decorator
