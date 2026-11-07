"""View decorators for account flows: registration protection (CORE.5), MFA exemption and
step-up (CORE.4)."""

import functools
import math
from collections.abc import Callable
from typing import Concatenate
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
from django.shortcuts import resolve_url
from django.urls import reverse

from apps.core.http import client_ip

from .captcha import captcha_passes
from .http import wants_json
from .mfa_session import is_recently_verified
from .services import has_second_factor, record_registration_attempt, registration_retry_after

TOO_MANY_ATTEMPTS = "Too many attempts. Please try again later."
VERIFICATION_FAILED = "The verification failed. Please reload the page and try again."


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
                TOO_MANY_ATTEMPTS,
                status=429,
                headers={"Retry-After": str(math.ceil(wait.total_seconds()))},
            )
        record_registration_attempt(ip)
        if not captcha_passes(request):
            return HttpResponseBadRequest(VERIFICATION_FAILED)
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
