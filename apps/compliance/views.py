"""Public compliance pages (CMP.5): the DSA points of contact (articles 11 and 12)."""

import datetime as dt
import math

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import get_language
from django.utils.translation import gettext as _
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.captcha import captcha_passes
from apps.core.audit import audit_context
from apps.core.http import client_ip
from apps.core.public_forms import Protection, public_form
from apps.core.services import NoPlatformOperator
from apps.orders.models import Order

from . import engine, services, withdrawal
from . import subprocessors as subprocessors_register
from .forms import URL_MAX, NoticeForm, WithdrawalLookupForm
from .models import Notice, WithdrawalStatement


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


@require_GET
@cache_control(public=True, max_age=300)
def subprocessors(request: HttpRequest) -> HttpResponse:
    """The sub-processor register (CMP.4): who receives personal data, why, where, under which
    safeguard. Static, reviewed data."""
    return render(
        request,
        "compliance/subprocessors.html",
        {"entries": subprocessors_register.public_register()},
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


# --- Online withdrawal (CMP.6 2/2)

WITHDRAWAL_SESSION_KEY = "compliance.withdrawal_statements"

_WITHDRAWAL_ERRORS = {
    "nothing_selected": _("Choose at least one part of the order to withdraw from."),
    "unknown_vendor_order": _("That part does not belong to this order."),
    "not_available": _("The withdrawal period for that part has ended."),
    "already_withdrawn": _("A withdrawal for that part was already recorded."),
}


def _no_store(response: HttpResponse) -> HttpResponse:
    response["Cache-Control"] = "no-store"
    return response


def _labels_context() -> dict[str, object]:
    return {
        "labels": engine.withdrawal_labels(get_language() or "en"),
        "platform_email": withdrawal.platform_contact_email(),
    }


def _render_lookup(request: HttpRequest, form: WithdrawalLookupForm) -> HttpResponse:
    return _no_store(
        render(request, "compliance/withdrawal_start.html", {"form": form, **_labels_context()})
    )


def _render_choice(
    request: HttpRequest, order: Order, *, token: str = "", error: str = "", status: int = 200
) -> HttpResponse:
    options = withdrawal.options(order)
    response = render(
        request,
        "compliance/withdrawal_choose.html",
        {
            "order": order,
            "options": options,
            "any_open": any(o.window.is_open and not o.withdrawn for o in options),
            "token": token or withdrawal.make_token(order),
            "error": error,
            **_labels_context(),
        },
        status=status,
    )
    return _no_store(response)


@public_form(
    protection=Protection.CAPTCHA,
    purpose="Withdrawal from a contract: identify the order (CMP.6)",
    task="CMP.6",
    rate_limit="per IP (lookup and captcha-failure budgets)",
    templates=("compliance/withdrawal_start.html",),
)
def withdrawal_start(request: HttpRequest) -> HttpResponse:
    """The first page of the withdrawal function, reachable by anyone for the whole period.

    The consumer enters the order number and the email on the order. A wrong pair gets the same
    answer whatever is wrong, so the page does not tell whether an order number exists.
    """
    if request.method != "POST":
        return _render_lookup(request, WithdrawalLookupForm(initial={"order_number": ""}))
    ip = client_ip(request)
    form = WithdrawalLookupForm(request.POST)
    if not captcha_passes(request):
        wait = withdrawal.captcha_retry_after(ip)
        if wait:
            return _too_many(wait)
        form.add_error(None, _("The verification failed. Please reload the page and try again."))
        return _render_lookup(request, form)
    wait = withdrawal.lookup_retry_after(ip)  # counted once the captcha passed
    if wait:
        return _too_many(wait)
    if form.is_valid():
        number = form.cleaned_data["order_number"]
        # Counted on every attempt; when spent, the answer is the generic one.
        limited = bool(withdrawal.order_lookup_retry_after(number, form.cleaned_data["email"]))
        order = None if limited else withdrawal.find_order(number, form.cleaned_data["email"])
        if order is not None:
            return _render_choice(request, order)
        form.add_error(None, _("We could not find an order with this number and email address."))
    return _render_lookup(request, form)


@require_POST
@public_form(
    protection=Protection.SIGNED_TOKEN,
    purpose="Withdrawal from a contract: confirm the statement (CMP.6)",
    task="CMP.6",
    reason=(
        "The signed token is minted only after the order number and email matched behind the "
        "captcha, expires after 30 minutes and names one order."
    ),
    rate_limit=(
        "the token needs a captcha-protected lookup, and a statement is recorded once per "
        "vendor order (unique), so a replay changes nothing"
    ),
)
def withdrawal_confirm(request: HttpRequest) -> HttpResponse:
    """The second step: 'confirm withdrawal'. Records one statement per chosen vendor order,
    shows the acknowledgement page; the same content goes to the buyer's email."""
    token = request.POST.get("token", "")
    order = withdrawal.read_token(token)
    if order is None:
        return redirect("withdrawal")
    if request.user.is_authenticated:
        ctx = audit_context(request)
    else:
        ctx = services.anonymous_context(client_ip(request))
    try:
        changes = withdrawal.submit_withdrawal(
            order, request.POST.getlist("vendor_orders"), ctx=ctx
        )
    except withdrawal.WithdrawalError as exc:
        return _render_choice(request, order, token=token, error=str(_WITHDRAWAL_ERRORS[exc.code]))
    request.session[WITHDRAWAL_SESSION_KEY] = [c.key for c in changes.changes]
    return redirect("withdrawal_done")


@require_GET
def withdrawal_done(request: HttpRequest) -> HttpResponse:
    """The acknowledgement page, shown from the visitor's own session (a 404 without one). The
    email is the durable medium; this page repeats its content."""
    references = request.session.get(WITHDRAWAL_SESSION_KEY) or []
    statements = list(
        WithdrawalStatement.objects.filter(public_id__in=references)
        .select_related("order", "vendor_order__vendor")
        .order_by("id")
    )
    if not statements:
        raise Http404("No withdrawal was just recorded")
    order = statements[0].order
    return _no_store(
        render(
            request,
            "compliance/withdrawal_done.html",
            {
                "order": order,
                "statements": statements,
                "submitted_at": withdrawal.display_time(statements[0].submitted_at),
                "emailed": all(s.acknowledged_at for s in statements),
                "vendor_contacts": [
                    (s.vendor_order.vendor.display_name, s.vendor_order.vendor.contact_email)
                    for s in statements
                    if s.vendor_order.vendor.contact_email
                ],
                **_labels_context(),
            },
        )
    )


@require_GET
@cache_control(public=True, max_age=300)
def consumer_information(request: HttpRequest) -> HttpResponse:
    """The pre-contractual information on the withdrawal right and where to exercise it (art.
    49(1)(h)), and the legal guarantee. Public and cached: the same for every visitor."""
    info = withdrawal.precontractual(get_language() or "en")
    return render(request, "compliance/consumer_information.html", {"info": info})
