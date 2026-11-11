"""Public compliance pages (CMP.5): the DSA points of contact (articles 11 and 12)."""

import datetime as dt
import math

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import get_language
from django.utils.translation import gettext as _
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET

from apps.accounts.captcha import captcha_passes
from apps.core.http import client_ip
from apps.core.public_forms import Protection, public_form
from apps.core.services import NoPlatformOperator

from . import services
from .forms import URL_MAX, NoticeForm
from .models import Notice


@require_GET
@cache_control(public=True, max_age=300)
def contact_points(request: HttpRequest) -> HttpResponse:
    """Who authorities and users contact, and in which languages, from the current operator.

    Public and cached for five minutes: the data is staff configuration that rarely changes.
    """
    try:
        points = services.contact_points()
    except NoPlatformOperator as exc:
        raise Http404("No platform operator is configured") from exc
    if not (points.authority_email or points.user_email or points.user_url):
        raise Http404("No contact is configured")  # a half-configured site publishes nothing
    return render(
        request,
        "compliance/contact_points.html",
        {
            "points": points,
            "authority_languages": [services.language_name(c) for c in points.authority_languages],
            "user_languages": [services.language_name(c) for c in points.user_languages],
        },
    )


# --- Notice and action (DSA art. 16, CMP.5 part 2)

NOTICE_SESSION_KEY = "compliance.notice_reference"


def _too_many(wait: dt.timedelta) -> HttpResponse:
    return HttpResponse(
        _("Too many reports from this address. Try again later."),
        status=429,
        headers={"Retry-After": str(math.ceil(wait.total_seconds()))},
    )


def _render_form(request: HttpRequest, form: NoticeForm) -> HttpResponse:
    response = render(request, "compliance/notice_form.html", {"form": form})
    response["Cache-Control"] = "no-store"
    return response


@public_form(
    protection=Protection.CAPTCHA,
    purpose="Report illegal content (DSA notice)",
    task="CMP.5",
    rate_limit="per IP (notice and captcha-failure budgets)",
    templates=("compliance/notice_form.html",),
)
def notice_form(request: HttpRequest) -> HttpResponse:
    """The public report form: no account needed, captcha and a per-address rate limit on POST.

    GET shows the form (`?url=` and `?kind=` prefill it, for the report links on pages). A
    refused captcha shows the form again; too many submissions answer 429 with Retry-After.
    """
    if request.method == "POST":
        ip = client_ip(request)
        form = NoticeForm(request.POST)
        if not captcha_passes(request):
            wait = services.captcha_retry_after(ip)
            if wait:
                return _too_many(wait)
            form.add_error(
                None, _("The verification failed. Please reload the page and try again.")
            )
            return _render_form(request, form)
        wait = services.notice_retry_after(ip)  # counted once the captcha passed
        if wait:
            return _too_many(wait)
        if form.is_valid():
            data = form.cleaned_data
            changes = services.submit_notice(
                target_kind=data["target_kind"],
                url=data["url"],
                category=data["category"],
                explanation=data["explanation"],
                notifier_name=data["notifier_name"],
                notifier_email=data["notifier_email"],
                good_faith=data["good_faith"],
                language=get_language() or "",
                ctx=services.public_notice_context(ip, data["category"]),
            )
            request.session[NOTICE_SESSION_KEY] = changes.changes[0].key  # the public id
            return redirect("dsa_notice_received")
    else:
        initial = {"url": request.GET.get("url", "")[:URL_MAX]}
        if request.GET.get("kind") in Notice.TargetKind.values:
            initial["target_kind"] = request.GET["kind"]
        form = NoticeForm(initial=initial)
    return _render_form(request, form)


@require_GET
def notice_received(request: HttpRequest) -> HttpResponse:
    """Thanks page with the reference, shown once after a submission."""
    reference = request.session.pop(NOTICE_SESSION_KEY, "")
    if not reference:
        raise Http404("No report was just sent")
    response = render(request, "compliance/notice_received.html", {"reference": reference})
    response["Cache-Control"] = "no-store"
    return response
