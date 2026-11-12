"""On-demand account activation pages (CORE.19). Thin adapters over `activation`.

The request form takes only an email address and answers the same redirect whatever happens
(known account, unknown address, staff account, rate-limited account). Protection of the
anonymous POST: per-IP registration rate limit (429, audited), Turnstile, CSRF, and a per
address limit. The set-password page answers only for a link opened in this session.
"""

import math
from collections.abc import Mapping

from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.cache import add_never_cache_headers
from django.views.decorators.http import require_http_methods

from apps.core.audit import audit_context
from apps.core.http import client_ip
from apps.core.i18n import language_switch_home
from apps.core.public_forms import Protection, public_form

from . import activation, services
from .decorators import protect_registration
from .forms import ActivationPasswordForm, ActivationRequestForm

TOO_MANY_ATTEMPTS = "Too many attempts. Please try again later."


def _page(
    request: HttpRequest, template: str, context: Mapping[str, object], status: int = 200
) -> HttpResponse:
    response = render(request, f"accounts/activation/{template}.html", context, status=status)
    response["Referrer-Policy"] = "strict-origin"
    response["X-Robots-Tag"] = "noindex, nofollow"
    add_never_cache_headers(response)
    return response


@public_form(
    protection=Protection.CAPTCHA,
    purpose="Activate your account: ask for the link that sets a migrated account's password",
    task="CORE.19",
    rate_limit="per IP (registration throttle) and per address (3 an hour)",
    templates=("accounts/activation/request.html",),
)
@require_http_methods(["GET", "POST"])
@protect_registration
def request_link(request: HttpRequest) -> HttpResponse:
    form = ActivationRequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        activation.request_activation_link(form.cleaned_data["email"], ctx=audit_context(request))
        return HttpResponseRedirect(reverse("activation:sent"))
    return _page(request, "request", {"form": form})


@require_http_methods(["GET"])
def sent(request: HttpRequest) -> HttpResponse:
    return _page(request, "sent", {})


def _invalid(request: HttpRequest) -> HttpResponse:
    context = {"request_url": reverse("activation:request")}
    return _page(request, "invalid", context, status=403)


@public_form(
    protection=Protection.SIGNED_TOKEN,
    purpose="Choose the password of a migrated account (CORE.19 link)",
    task="CORE.19",
    reason="the link must have been opened in this session from its one-time signed token",
    rate_limit="per IP (action link throttle)",
)
@language_switch_home
@require_http_methods(["GET", "HEAD", "POST"])
def set_password(request: HttpRequest, user_key: str) -> HttpResponse:
    link = services.email_bound_link(request.session, activation.PURPOSE, user_key)
    if link.refusal is not None:
        return _invalid(request)
    if request.method == "POST":
        wait = services.action_link_retry_after(client_ip(request))
        if wait:
            retry = str(max(1, math.ceil(wait.total_seconds())))
            return HttpResponse(TOO_MANY_ATTEMPTS, status=429, headers={"Retry-After": retry})
    form = ActivationPasswordForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            activation.complete_activation(
                link, password=form.cleaned_data["password"], ctx=audit_context(request)
            )
        except ValidationError as exc:  # a weak password: the link stays usable
            form.add_error("password", exc)
        except activation.ActivationLinkInvalid:
            return _invalid(request)
        else:
            services.forget_email_bound_link(request.session, activation.PURPOSE, user_key)
            return _page(request, "done", {"sign_in_url": reverse("login")})
    return _page(request, "set_password", {"form": form})
