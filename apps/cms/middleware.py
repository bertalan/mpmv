"""Nonces for Wagtail's date and time pickers in the editors' admin (CMS.1, see `csp`).

Listed right after Django's ContentSecurityPolicyMiddleware, so it handles the response first:
using the nonce here puts it in the CSP header that middleware writes next.
"""

import datetime as dt
import math
from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponseBadRequest
from django.http.response import HttpResponse, HttpResponseBase
from django.middleware.csp import get_nonce
from django.urls import NoReverseMatch, reverse
from django.utils.translation import gettext
from wagtail.models import PageViewRestriction

from apps.accounts.captcha import captcha_passes
from apps.accounts.decorators import TOO_MANY_ATTEMPTS, VERIFICATION_FAILED
from apps.core.http import client_ip
from apps.core.services import consume_rate_limit, rate_limit_wait
from apps.core.throttling import ip_bucket

from .csp import nonce_picker_scripts


def _in_wagtail_admin(request: HttpRequest) -> bool:
    try:
        return request.path.startswith(reverse("wagtailadmin_home"))
    except NoReverseMatch:  # a URLconf without the Wagtail admin (some tests)
        return False


class WagtailAdminPickerNonceMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponseBase]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        response = self.get_response(request)
        nonce = get_nonce(request)
        if (
            nonce is None
            or not isinstance(response, HttpResponse)
            or not response.get("Content-Type", "").startswith("text/html")
            or not _in_wagtail_admin(request)
        ):
            return response
        html, changed = nonce_picker_scripts(response.content.decode(response.charset), str(nonce))
        if changed:
            response.content = html.encode(response.charset)
        return response


# Wagtail's page view restriction forms (a page shared by password or login) are anonymous POSTs
# (SEC.14, spec 12). Run as `process_view`, after CsrfViewMiddleware, so a request without a valid
# CSRF token is refused first and does not use up a bucket.
PASSWORD_ROUTE = "wagtailcore_authenticate_with_password"  # noqa: S105 (a URL name)
LOGIN_ROUTE = "wagtailcore_login"
RESTRICTION_WINDOW = dt.timedelta(minutes=15)


def _too_many(wait: dt.timedelta) -> HttpResponse:
    retry = str(max(1, math.ceil(wait.total_seconds())))
    return HttpResponse(
        gettext(TOO_MANY_ATTEMPTS),
        status=429,
        headers={"Retry-After": retry},
        content_type="text/plain",
    )


class PageRestrictionProtectionMiddleware:
    """Rate limit and captcha for the two POST routes of Wagtail's page privacy.

    Password route, all before the view runs, so a refused request stores nothing in the session:
    a bucket per IP (`PAGE_RESTRICTION_IP_LIMIT`, every POST counts), then the restriction's
    bucket (`PAGE_RESTRICTION_LIMIT`), then the captcha. The restriction's bucket counts only
    wrong passwords (the form shown again, status 200) and has a higher threshold than the IP
    bucket: other people's traffic, a failed captcha or a good password never use it up, so
    locking a page's visitors out takes captcha-solved wrong guesses (the trade-off is written in
    docs/dev/public-forms.md). An unknown restriction id creates no bucket. The captcha is checked
    here, whatever template a page type renders. Login route: the captcha and the lockout already
    run inside `authenticate()` (LoginProtectionBackend); only the per-IP bucket is added (a
    second verify would spend the single-use token).
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponseBase]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        response = self.get_response(request)
        key = getattr(request, "_page_restriction_key", None)
        if key and response.status_code == 200:  # the password form again: a wrong password
            consume_rate_limit(
                key, limit=settings.PAGE_RESTRICTION_LIMIT, window=RESTRICTION_WINDOW
            )
        return response

    def process_view(
        self,
        request: HttpRequest,
        view_func: Callable[..., HttpResponseBase],
        view_args: tuple[Any, ...],
        view_kwargs: dict[str, Any],
    ) -> HttpResponseBase | None:
        match = request.resolver_match
        if (
            request.method != "POST"
            or match is None
            or match.url_name
            not in {
                PASSWORD_ROUTE,
                LOGIN_ROUTE,
            }
        ):
            return None
        ip = ip_bucket(client_ip(request)) or "unknown"
        wait = consume_rate_limit(
            f"page-restriction:ip:{ip}",
            limit=settings.PAGE_RESTRICTION_IP_LIMIT,
            window=RESTRICTION_WINDOW,
        )
        if wait:
            return _too_many(wait)
        if match.url_name == LOGIN_ROUTE:
            return None
        restriction_id = view_kwargs.get("page_view_restriction_id")
        if not PageViewRestriction.objects.filter(pk=restriction_id).exists():
            return None  # the view answers 404; nothing is stored for an id that does not exist
        key = f"page-restriction:restriction:{restriction_id}"
        wait = rate_limit_wait(key, limit=settings.PAGE_RESTRICTION_LIMIT)
        if wait:
            return _too_many(wait)
        if not captcha_passes(request):
            return HttpResponseBadRequest(gettext(VERIFICATION_FAILED), content_type="text/plain")
        request._page_restriction_key = key  # type: ignore[attr-defined]
        return None
