"""Manager shop space: the shop's holidays and closures (VEN.20).

A vendor setting with no free text and no review: a change takes effect at once and buyers see a
neutral notice with the dates (spec 04). Thin adapter over `apps.vendors.services`. Every row is
read through `self.scoped`, so another shop's closure is a 404, never a refusal that confirms it
exists. "Today" is `business_today()`, the same date the notice and the checkout use.
"""

import uuid
from dataclasses import replace
from typing import Any

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import QuerySet
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.translation import gettext as _

from apps.accounts.decorators import requires_level_method
from apps.core.audit import AuditContext, audit_context
from apps.core.models import AuditLog
from apps.vendors import services
from apps.vendors.models import Vendor, VendorClosure

from .forms import ClosureForm
from .views import ManagerView

SECTION = "shop.closures"
LIST_URL = "manager:shop:closures"
GATE_ACTION = "content_edit"  # group 1: any session, CORE.26 can tighten it (CORE.25)


class _ClosureView(ManagerView):
    section = SECTION

    def vendor(self) -> Vendor:
        if self.manager.vendor is None:
            raise Http404("No vendor selected.")
        return self.manager.vendor

    audit_source = AuditLog.Source.VENDOR

    def audit(self, request: HttpRequest) -> AuditContext:
        return replace(audit_context(request), source=self.audit_source)

    # The URLs of the page, so the staff page in the platform space reuses every view and template.
    def list_url(self) -> str:
        return reverse(LIST_URL)

    def edit_url(self, closure: VendorClosure) -> str:
        return reverse("manager:shop:closure_edit", args=[closure.public_id])

    def remove_url(self, closure: VendorClosure) -> str:
        return reverse("manager:shop:closure_remove", args=[closure.public_id])

    def rows(self) -> QuerySet[VendorClosure]:
        """The closures this page may touch: the selected shop's."""
        return self.scoped(VendorClosure)

    def closure(self) -> VendorClosure:
        """The closure of the URL in the selected shop; another shop's is a 404."""
        public_id: uuid.UUID = self.kwargs["public_id"]
        return get_object_or_404(self.rows(), public_id=public_id)

    def fail(self, form: ClosureForm, error: ValidationError) -> None:
        for name, problems in error.message_dict.items():
            form.add_error(name if name in form.fields else None, problems)


class ClosuresView(_ClosureView):
    """Current and upcoming ranges, the ones that have ended, and the form to add one."""

    template_name = "manager/shop/closures.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        today = services.business_today()
        rows = list(self.rows().order_by("start_date", "end_date"))
        current = [c for c in rows if c.repeats_yearly or c.end_date >= today]
        context["current"] = [(c, self.edit_url(c), self.remove_url(c)) for c in current]
        context["history"] = [c for c in rows if c not in current]
        context["vendor_id"] = self.vendor().pk
        context["form_action"] = self.list_url()
        context.setdefault("form", ClosureForm())
        return context

    @requires_level_method(GATE_ACTION, shop=lambda view, request: view.manager.vendor)
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        vendor = self.vendor()
        form = ClosureForm(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        try:
            services.add_closure(
                vendor,
                **form.cleaned_data,
                ctx=self.audit(request),
                today=services.business_today(),
            )
        except ValidationError as error:
            self.fail(form, error)
            return self.render_to_response(self.get_context_data(form=form))
        messages.success(request, _("The closure is saved. Buyers see the notice from now on."))
        return HttpResponseRedirect(self.list_url())


class ClosureEditView(_ClosureView):
    template_name = "manager/shop/closure_form.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        closure = self.closure()
        context["closure"] = closure
        context["list_url"] = self.list_url()
        context["title"] = _("Edit a closure")
        context.setdefault(
            "form",
            ClosureForm(
                initial={
                    "start_date": closure.start_date,
                    "end_date": closure.end_date,
                    "repeats_yearly": closure.repeats_yearly,
                    "notice_days": closure.notice_days,
                }
            ),
        )
        return context

    @requires_level_method(GATE_ACTION, shop=lambda view, request: view.manager.vendor)
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        closure = self.closure()
        form = ClosureForm(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        try:
            services.update_closure(
                closure,
                **form.cleaned_data,
                ctx=self.audit(request),
                today=services.business_today(),
            )
        except ValidationError as error:
            self.fail(form, error)
            return self.render_to_response(self.get_context_data(form=form))
        messages.success(request, _("The closure is saved. Buyers see the notice from now on."))
        return HttpResponseRedirect(self.list_url())


class ClosureDeleteView(_ClosureView):
    """GET asks for the confirmation, POST removes the range."""

    template_name = "manager/shop/closure_remove.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["closure"] = self.closure()
        context["title"] = _("Remove a closure")
        context["list_url"] = self.list_url()
        return context

    @requires_level_method(GATE_ACTION, shop=lambda view, request: view.manager.vendor)
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        closure = self.closure()
        try:
            services.delete_closure(
                closure, ctx=self.audit(request), today=services.business_today()
            )
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
        else:
            messages.success(request, _("The closure is removed."))
        return HttpResponseRedirect(self.list_url())
