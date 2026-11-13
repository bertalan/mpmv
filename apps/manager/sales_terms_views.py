"""Manager shop space: the seller's versioned sales terms (VEN.35).

The owner drafts and publishes a version; the version in force and the history are listed.
Thin adapter over `apps.vendors.terms_services`. Publishing is the group 2 session-level action
`sales_terms_publish` (CORE.25).
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
from apps.vendors import terms_engine
from apps.vendors import terms_services as services
from apps.vendors.models import SalesTermsVersion

from .forms import SalesTermsForm
from .views import ManagerView

SECTION = "shop.sales_terms"


class SalesTermsView(ManagerView):
    section = SECTION
    template_name = "manager/shop/sales_terms.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        vendor = self.manager.vendor
        assert vendor is not None  # noqa: S101  # the section needs a vendor
        current = services.version_in_force(vendor)
        initial: dict[str, Any] = {"governing_law": str(vendor.country)}
        if current is not None:
            initial = {
                "body": current.body,
                "clauses": terms_engine.clauses_text(current.clauses),
                "governing_law": str(current.governing_law),
                "b2b_court": current.b2b_court,
                "delivery_days": current.delivery_days,
                "return_cost_bearer": current.return_cost_bearer,
                "return_cost_info": current.return_cost_info,
            }
        context.setdefault("form", SalesTermsForm(initial=initial))
        context["current"] = current
        context["versions"] = self.scoped(SalesTermsVersion).order_by("-number")
        context["link"] = services.terms_url(current) if current is not None else ""
        context["template_clause"] = _(
            "These terms are governed by the law of [country]. Disputes with business buyers "
            "are decided by the court of [city]. For consumers, the court of their residence "
            "is always competent."
        )
        return context

    @requires_level_method("sales_terms_publish")  # group 2 (CORE.25)
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        vendor, actor = self.manager.vendor, self.manager.user
        if vendor is None or not isinstance(actor, User):
            raise Http404("No vendor selected.")
        form = SalesTermsForm(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        data = dict(form.cleaned_data)
        data["clauses"] = terms_engine.parse_clauses(data["clauses"])
        ctx = replace(audit_context(request), source=AuditLog.Source.VENDOR)
        try:
            services.publish_sales_terms(vendor, data, actor=actor, ctx=ctx)
        except ValidationError as error:
            for name, messages_ in error.message_dict.items():
                form.add_error(name if name in form.fields else None, messages_)
            return self.render_to_response(self.get_context_data(form=form))
        except PermissionDenied:
            return self.render_to_response(self.get_context_data(form=form), status=403)
        messages.success(request, _("Your sales terms are published and in force."))
        return HttpResponseRedirect(reverse("manager:shop:sales_terms"))
