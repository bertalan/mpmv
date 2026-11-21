"""Stray payments in the Manager platform space (PAY.4): a thin adapter over `payments.stray`.

The open stray-payment alerts, with the reason, the amount, the age and whether the provider
confirms a full refund; support closes one by hand with a reason once it is settled, so the
nightly job stops reminding. Finance reads the list; a close by Finance answers 403 (CAT.30),
another role or a vendor member gets 404. The service checks the permission again and keeps the
reason's hash only. Closing moves no money, so there is no passkey step-up.
"""

import datetime as dt
import logging
import math
from typing import Any, Final

from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.accounts.models import User
from apps.core.audit import audit_context
from apps.core.services import consume_rate_limit
from apps.payments import stray

from .fitment_views import WriteGate, writer
from .views import ManagerView

logger = logging.getLogger(__name__)

SECTION: Final = "platform.stray_payments"  # a row of roles.MATRIX
# "Check now" makes one provider call: a few per user and 10 minutes cover a support session
# that works through the list, and keep a stuck key or a script off the provider's API.
CHECK_LIMIT: Final = 10
CHECK_WINDOW: Final = dt.timedelta(minutes=10)

REASON_LABELS: Final = {
    "late": gettext_lazy("Order no longer awaiting payment"),
    "mismatch": gettext_lazy("Amount or currency mismatch"),
    "wrong_reference": gettext_lazy("Wrong payment reference"),
    "unmatched": gettext_lazy("No matching order"),
}


class CloseAlertForm(forms.Form):
    payment = forms.CharField(max_length=255, widget=forms.HiddenInput)
    reason = forms.CharField(
        label=gettext_lazy("Reason for closing"),
        max_length=500,
    )


class CheckRefundForm(forms.Form):
    payment = forms.CharField(max_length=255, widget=forms.HiddenInput)


class StrayPaymentsView(WriteGate, ManagerView):
    """The open alerts (GET, no provider call), the manual close of one (POST, CSRF, a reason)
    and "Check now" (POST `action=check`: one provider call for that alert, recorded)."""

    section = SECTION
    write_section = SECTION
    refuse_read_only = True  # Finance reads the list; its POST answers 403
    template_name = "manager/platform/stray_payments.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["alerts"] = [
            {"alert": alert, "reason": REASON_LABELS.get(alert.reason, alert.reason)}
            for alert in stray.open_alerts()
        ]
        context["can_close"] = writer(SECTION)(self.manager)
        context.setdefault("form_errors", None)
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        actor = self.manager.user
        if not isinstance(actor, User):
            raise Http404("No Manager page here.")
        if request.POST.get("action") == "check":
            return self._check(request, actor)
        form = CloseAlertForm(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form_errors=form), status=400)
        try:  # PermissionDenied from the service answers 403
            changes = stray.close_stray_capture(
                form.cleaned_data["payment"],
                actor=actor,
                reason=form.cleaned_data["reason"],
                ctx=audit_context(request),
            )
        except ValidationError as error:
            form.add_error("reason", " ".join(error.messages))
            return self.render_to_response(self.get_context_data(form_errors=form), status=400)
        if changes.is_empty:
            messages.info(request, _("This alert was already closed."))
        else:
            messages.success(request, _("The alert is closed. Support gets no more reminders."))
        return HttpResponseRedirect(reverse("manager:platform:stray_payments"))

    def _check(self, request: HttpRequest, actor: User) -> HttpResponse:
        form = CheckRefundForm(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form_errors=form), status=400)
        wait = consume_rate_limit(
            f"stray_check:user:{actor.pk}", limit=CHECK_LIMIT, window=CHECK_WINDOW
        )
        if wait:
            # The check itself is audited (`stray_checked`); a refused one changes nothing.
            logger.warning("stray-payment check throttled for user %s", actor.pk)
            retry = str(max(1, math.ceil(wait.total_seconds())))
            return HttpResponse(
                _("Too many attempts. Please try again later."),
                status=429,
                headers={"Retry-After": retry},
            )
        changes = stray.check_refund_now(  # PermissionDenied answers 403
            form.cleaned_data["payment"], actor=actor, ctx=audit_context(request)
        )
        if changes.is_empty:
            messages.info(request, _("This alert was already closed."))
        else:
            after = changes.changes[0].after or {}
            refunded = after.get("refunded")
            if refunded is None:
                messages.warning(
                    request, _("The payment provider could not be reached. Please try again later.")
                )
            else:
                messages.info(request, _("The refund state is checked and recorded."))
        return HttpResponseRedirect(reverse("manager:platform:stray_payments"))
