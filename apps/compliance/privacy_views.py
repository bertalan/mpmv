"""Customer self-service GDPR (CMP.4 2/2, CMP.30) and the staff view that processes requests.

The target is always `request.user`: no view reads a user id from the input. Every action is a
POST (CSRF is enforced by the middleware), rate limited per account, and needs a fresh
re-authentication: the account password, or a second factor verified within
`MFA_STEP_UP_MAX_AGE`. Audit rows come from the services (`gdpr`) and hold public ids only.
"""

import datetime as dt
import math
import uuid
from collections.abc import Callable
from typing import Any, cast

from django.conf import settings
from django.contrib.auth import logout
from django.contrib.auth.views import redirect_to_login
from django.db.models import F
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render, resolve_url
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.decorators import mfa_redirect, step_up_required
from apps.accounts.mfa_session import is_recently_verified
from apps.accounts.models import User
from apps.accounts.services import has_second_factor
from apps.core.audit import audit_context, record
from apps.core.public_forms import not_anonymous
from apps.core.services import consume_rate_limit
from apps.orders import engine

from . import gdpr
from .models import ErasureRequest

EXPORT_LIMIT = (3, dt.timedelta(hours=1))
ERASE_LIMIT = (5, dt.timedelta(hours=1))
# Why staff ran a pending request now. A fixed list, never free text: the audit trail must not
# collect personal data again (lane-a decision on PR 266).
REASONS: dict[str, str] = {
    "customer_written_request": gettext_noop("Customer asked in writing"),
    "customer_support_case": gettext_noop("Customer asked through a support case"),
    "open_order_closed": gettext_noop("Last open order is closed"),
    "legal_deadline": gettext_noop("Legal deadline is near"),
}
ACTION_PROCESSED = "compliance.erasure_processed"
PERMISSION = "compliance.change_erasurerequest"

type View = Callable[..., HttpResponse]


def _signed_in(view: View) -> View:
    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), resolve_url(settings.MFA_LOGIN_URL))
        return view(request, *args, **kwargs)

    wrapped.__name__ = view.__name__
    wrapped.__doc__ = view.__doc__
    return wrapped


def _too_many(wait: dt.timedelta) -> HttpResponse:
    return HttpResponse(
        _("Too many attempts. Please try again later."),
        status=429,
        headers={"Retry-After": str(math.ceil(wait.total_seconds()))},
    )


def _limited(user: User, scope: str, limit: tuple[int, dt.timedelta]) -> HttpResponse | None:
    wait = consume_rate_limit(f"privacy:{scope}:{user.public_id}", limit=limit[0], window=limit[1])
    return _too_many(wait) if wait else None


def _reauth_refusal(request: HttpRequest, *, factor_required: bool = False) -> HttpResponse | None:
    """None when the caller is freshly re-authenticated, else the response to send.

    An account without a usable password (passkey-only) cannot type one: it is sent to the
    second-factor step and comes back to the account page. With `factor_required` (erasure,
    D-32) an account that has a second factor must verify it again, a password is not enough.
    """
    user = cast(User, request.user)
    if is_recently_verified(request):
        return None
    if factor_required and has_second_factor(user):
        return mfa_redirect(request, "mfa:verify", next_url=reverse("privacy_account"))
    if not user.has_usable_password():
        target = "mfa:verify" if has_second_factor(user) else "mfa:setup"
        return mfa_redirect(request, target, next_url=reverse("privacy_account"))
    password = request.POST.get("password", "")
    if password and user.check_password(password):
        return None
    return HttpResponse(_("Please confirm your password."), status=403)


def _page(request: HttpRequest, *, status: int = 200, **extra: Any) -> HttpResponse:
    user = cast(User, request.user)
    context = {
        "years": engine.FISCAL_RETENTION_YEARS,
        "has_password": user.has_usable_password(),
        "has_factor": has_second_factor(user),
        **extra,
    }
    return render(request, "compliance/privacy_account.html", context, status=status)


@require_GET
@never_cache
@_signed_in
def account(request: HttpRequest) -> HttpResponse:
    return _page(request)


@not_anonymous(
    reason="signed-in users only; the target is always the caller",
    purpose="own data export",
    task="CMP.4",
)
@require_POST
@_signed_in
def download_data(request: HttpRequest) -> HttpResponse:
    """Data export (articles 15 and 20): the signed-in user's own data as a JSON download."""
    user = cast(User, request.user)
    if limited := _limited(user, "export", EXPORT_LIMIT):
        return limited
    if refusal := _reauth_refusal(request):
        return refusal
    body = gdpr.export_customer_json(user, ctx=audit_context(request))
    response = HttpResponse(body, content_type="application/json")
    response["Content-Disposition"] = 'attachment; filename="mpmv-data-export.json"'
    response["Cache-Control"] = "no-store, private"
    return response


@not_anonymous(
    reason="signed-in users only; the target is always the caller",
    purpose="own account erasure",
    task="CMP.4",
)
@require_POST
@_signed_in
def erase(request: HttpRequest) -> HttpResponse:
    """Account erasure (article 17, D-30): accepted at once; the order data stays restricted for
    the legal period; with an open order the rest completes when it closes."""
    user = cast(User, request.user)
    if limited := _limited(user, "erase", ERASE_LIMIT):
        return limited
    if refusal := _reauth_refusal(request, factor_required=True):
        return refusal
    if request.POST.get("confirm") != "erase":
        return _page(request, status=400, error=_("Tick the confirmation to erase your account."))
    try:
        gdpr.anonymise_customer(user, ctx=audit_context(request))
    except gdpr.VendorLinkedUser:
        return _page(
            request,
            vendor_linked=True,
            error=_("Your account belongs to a vendor. Contact support to close it."),
        )
    pending = not gdpr.is_anonymised(user)
    logout(request)
    return redirect(reverse("privacy_erase_done") + ("?pending=1" if pending else ""))


@require_GET
@never_cache
def erase_done(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "compliance/privacy_erased.html",
        {"pending": request.GET.get("pending") == "1", "years": engine.FISCAL_RETENTION_YEARS},
    )


# --- staff -------------------------------------------------------------------------------------


def _staff(view: View) -> View:
    @_signed_in
    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        user = request.user
        if not (user.is_active and user.is_staff and user.has_perm(PERMISSION)):
            raise Http404
        return view(request, *args, **kwargs)

    wrapped.__name__ = view.__name__
    wrapped.__doc__ = view.__doc__
    return wrapped


@require_GET
@never_cache
@_staff
def requests_list(request: HttpRequest) -> HttpResponse:
    """Erasure requests by public id and state; never a name or an address."""
    rows = ErasureRequest.objects.order_by(F("completed_at").asc(nulls_first=True), "requested_at")[
        :200
    ]
    return render(
        request, "compliance/privacy_requests.html", {"rows": rows, "reasons": REASONS.items()}
    )


@not_anonymous(
    reason="signed-in users only; the target is always the caller",
    purpose="staff processes an erasure request",
    task="CMP.4",
)
@require_POST
@_staff
@step_up_required()
def request_process(request: HttpRequest, public_id: uuid.UUID) -> HttpResponse:
    """Run a pending request now (the nightly job does it too), with a reason, audited."""
    erasure = get_object_or_404(ErasureRequest.objects.select_related("user"), public_id=public_id)
    reason = request.POST.get("reason", "")
    if reason not in REASONS:
        return HttpResponseBadRequest(_("Choose one of the listed reasons."))
    ctx = audit_context(request)
    try:
        gdpr.anonymise_customer(erasure.user, ctx=ctx)
    except gdpr.VendorLinkedUser:
        return HttpResponse(_("This account belongs to a vendor."), status=409)
    erasure.refresh_from_db()
    record(
        ctx,
        ACTION_PROCESSED,
        "compliance.erasurerequest",
        str(erasure.public_id),
        {},
        {"completed": erasure.completed_at is not None, "reason": reason},
    )
    return redirect(reverse("privacy_requests"))
