"""The public "Sell with us" application (VEN.22, owner decision Q-55). Thin adapters over
`services.apply_to_sell` and `services.confirm_application`.

The form creates the applicant's account and application in one step, but the account stays
inactive: only the emailed link (CORE.15, email-bound) activates it, and the applicant then
enrols a second factor at first login (CORE.4). An application never signs anyone in.

Protection of the anonymous POST, in order: a body-size cap, the per-IP registration rate limit
(429 + Retry-After, audited), Turnstile (no puzzle, CMP.9), CSRF (Django middleware), field
validation. The answer is the same redirect whether or not the address already has an account.
Pages are never cached or indexed and send no path in the Referer.
"""

import functools
import math
from collections.abc import Callable
from typing import Any

from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.cache import add_never_cache_headers
from django.utils.translation import get_language
from django.views.decorators.http import require_http_methods

from apps.accounts.decorators import protect_registration
from apps.core.audit import audit_context
from apps.core.http import client_ip
from apps.core.i18n import language_switch_home

from . import services
from .forms import MAX_BODY_BYTES, ApplicationForm, ConfirmForm

TOO_MANY_ATTEMPTS = "Too many attempts. Please try again later."
TOO_LARGE = "The request is too large."

# Vendor model fields -> the form field that holds their value (others: a form-wide error).
FORM_FIELD_OF = {
    "legal_name": "business_name",
    "display_name": "business_name",
    "contact_email": "email",
    "country": "country",
    "slug": "business_name",
}

type View = Callable[..., HttpResponse]


def _private(response: HttpResponse) -> HttpResponse:
    response["Referrer-Policy"] = "strict-origin"
    response["X-Robots-Tag"] = "noindex, nofollow"
    add_never_cache_headers(response)
    return response


def _page(
    request: HttpRequest, template: str, context: dict[str, Any], status: int = 200
) -> HttpResponse:
    return _private(render(request, f"vendors/apply/{template}.html", context, status=status))


def limit_body(view: View) -> View:
    """Refuse a request whose declared body is over the cap, before anything is parsed."""

    @functools.wraps(view)
    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        try:
            length = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = MAX_BODY_BYTES + 1
        if length > MAX_BODY_BYTES:
            return HttpResponse(TOO_LARGE, status=413)
        return view(request, *args, **kwargs)

    return wrapped


@require_http_methods(["GET", "POST"])
@limit_body
@protect_registration
def apply(request: HttpRequest) -> HttpResponse:
    form = ApplicationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            services.apply_to_sell(
                full_name=data["full_name"],
                email=data["email"],
                business_name=data["business_name"],
                country=data["country"],
                language=get_language() or "",
                ctx=audit_context(request),
            )
        except ValidationError as exc:
            # Raised before the address is looked up: it never depends on an account existing.
            for name, errors in exc.update_error_dict({}).items():
                form.add_error(FORM_FIELD_OF.get(name), errors)
        else:
            return _private(HttpResponseRedirect(reverse("vendors:apply_sent")))
    return _page(request, "form", {"form": form}, status=200)


@require_http_methods(["GET"])
def sent(request: HttpRequest) -> HttpResponse:
    return _page(request, "sent", {})


def _refused(request: HttpRequest) -> HttpResponse:
    return _page(request, "invalid", {}, status=403)


@language_switch_home
@require_http_methods(["GET", "HEAD", "POST"])
def confirm(request: HttpRequest, user_key: str) -> HttpResponse:
    """Where the emailed link leads, after its Continue page opened it in this session."""
    link = services.application_link(request.session, user_key)
    if link.refusal is not None:
        return _refused(request)
    if request.method == "POST":
        # After the link check, as for the claim page: a request with no opened link is refused
        # for free, and the Continue page that opens a link is limited per IP (CORE.15).
        wait = services.application_retry_after(client_ip(request))
        if wait:
            retry = str(max(1, math.ceil(wait.total_seconds())))
            response = HttpResponse(TOO_MANY_ATTEMPTS, status=429, headers={"Retry-After": retry})
            return _private(response)
    form = ConfirmForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.confirm_application(
                link, password=form.cleaned_data["password"], ctx=audit_context(request)
            )
        except ValidationError as exc:  # a weak password: the link stays usable
            form.add_error("password", exc)
        except services.ApplicationLinkInvalid:
            return _refused(request)
        else:
            return _page(request, "confirmed", {"sign_in_url": reverse("login")})
    return _page(request, "confirm", {"form": form})


@language_switch_home
@require_http_methods(["GET", "HEAD", "POST"])
def cancel(request: HttpRequest, user_key: str) -> HttpResponse:
    """ "This was not me": the page the cancel link of every application mail leads to.

    A GET (or HEAD, or a mail scanner's prefetch) only shows a button; the deletion runs on the
    POST, with CSRF. Whatever the state (no link opened, already cancelled, confirmed, purged,
    a random key) the answer is the same neutral page, so nothing is learned from it."""
    link = services.application_cancel_link(request.session, user_key)
    if link.refusal is not None:
        return _page(request, "cancelled", {})
    if request.method != "POST":
        return _page(request, "cancel", {})
    wait = services.application_retry_after(client_ip(request))
    if wait:
        retry = str(max(1, math.ceil(wait.total_seconds())))
        response = HttpResponse(TOO_MANY_ATTEMPTS, status=429, headers={"Retry-After": retry})
        return _private(response)
    services.cancel_application(link, ctx=audit_context(request))
    return _page(request, "cancelled", {})
