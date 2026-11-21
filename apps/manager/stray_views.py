"""Stray payments in the Manager platform space (PAY.4): a thin adapter over `payments.stray`.

The open stray-payment alerts, with the reason, the amount, the age and whether the provider
confirms a full refund; support closes one by hand with a reason once it is settled, so the
nightly job stops reminding. Finance reads the list; a close by Finance answers 403 (CAT.30),
another role or a vendor member gets 404. The service checks the permission again and keeps the
reason's hash only. Closing moves no money, so there is no passkey step-up.
"""

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
from apps.payments import stray

from .fitment_views import WriteGate, writer
from .views import ManagerView

SECTION: Final = "platform.stray_payments"  # a row of roles.MATRIX

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


class StrayPaymentsView(WriteGate, ManagerView):
    """The open alerts (GET) and the manual close of one (POST, CSRF, a reason)."""

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
