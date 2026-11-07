"""View decorators for account flows (CORE.5)."""

import functools
import math
from collections.abc import Callable
from typing import Concatenate

from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest

from apps.core.http import client_ip

from .captcha import captcha_passes
from .services import record_registration_attempt, registration_retry_after

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
