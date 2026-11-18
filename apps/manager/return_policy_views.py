"""Manager shop space: the seller's return policy (VEN.40).

The owner proposes a version; it is private until platform staff approve it. The page shows the
policy the product page states (the approved one, or the default) next to the owner's latest
proposal. Thin adapter over `apps.vendors.return_services`. Proposing is the group 2 session
action `sales_terms_publish` (CORE.25): the return policy is part of the sales terms.
"""

from dataclasses import replace
from typing import Any

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import gettext as _

from apps.accounts.decorators import requires_level_method
from apps.accounts.models import User
from apps.core.audit import audit_context
from apps.core.models import AuditLog
from apps.vendors import return_services as services
from apps.vendors.models import ReturnPolicyVersion

from .forms import ReturnPolicyForm
from .views import ManagerView

SECTION = "shop.return_policy"


class ReturnPolicyView(ManagerView):
    section = SECTION
    template_name = "manager/shop/return_policy.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        vendor = self.manager.vendor
        assert vendor is not None  # noqa: S101  # the section needs a vendor
        latest = self.scoped(ReturnPolicyVersion).order_by("-number").first()
        effective = services.effective_return_policy(vendor)
        shown = latest if latest is not None else effective
        context.setdefault(
            "form",
            ReturnPolicyForm(
                initial={
                    "return_window_days": shown.return_window_days,
                    "return_shipping_paid_by": shown.return_shipping_paid_by,
                    "return_methods": list(shown.return_methods),
                    "returns_page_url": shown.returns_page_url,
                    "return_country": str(shown.return_country),
                }
            ),
        )
        context["public"] = effective
        context["latest"] = latest
        context["versions"] = self.scoped(ReturnPolicyVersion).order_by("-number")
        return context

    # Group 2 (CORE.25); the shop owner confirms it, once or in a window.
    @requires_level_method("sales_terms_publish", shop=lambda view, request: view.manager.vendor)
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        vendor, actor = self.manager.vendor, self.manager.user
        if vendor is None or not isinstance(actor, User):
            raise Http404("No vendor selected.")
        form = ReturnPolicyForm(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        ctx = replace(audit_context(request), source=AuditLog.Source.VENDOR)
        try:
            services.propose_return_policy(vendor, dict(form.cleaned_data), actor=actor, ctx=ctx)
        except ValidationError as error:
            for name, messages_ in error.message_dict.items():
                form.add_error(name if name in form.fields else None, messages_)
            return self.render_to_response(self.get_context_data(form=form))
        except PermissionDenied:
            return self.render_to_response(self.get_context_data(form=form), status=403)
        messages.success(request, _("Your return policy is sent for review."))
        return HttpResponseRedirect(reverse("manager:shop:return_policy"))
