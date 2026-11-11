"""Manager: the 30 days' notice before a vendor's service ends (P2B art. 4, CMP.5 part 4).

Platform space: staff list, give and withdraw notices (section check `compliance.review_notices`;
the two writes are the group 2 session-level action `termination_notice`, CORE.25). Shop space: the
vendor's owner sees the reason and the dates during the notice period and can download its data.
Thin adapters over `apps.compliance.services`.
"""

import datetime as dt
import json
import uuid
from dataclasses import replace
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.http.response import HttpResponseBase
from django.urls import reverse
from django.utils.translation import gettext as _

from apps.accounts.decorators import requires_level
from apps.compliance import engine, services
from apps.compliance.forms import TerminationForm, WithdrawTerminationForm
from apps.compliance.models import TerminationNotice
from apps.core.audit import audit_context
from apps.core.models import AuditLog
from apps.vendors.models import Vendor

from .views import ManagerView

PLATFORM_SECTION = "platform.terminations"  # a row of roles.MATRIX (CORE.18)
SHOP_SECTION = "shop.termination"
#: The session-level action (CORE.25) of giving or withdrawing a notice: group 2 by default.
LEVEL_ACTION = "termination_notice"


def can_give_notice(ctx: Any) -> bool:
    return services.can_review_notices(ctx.user)


def _effective_at(day: dt.date | None) -> dt.datetime | None:
    """The start of `day` in the business time zone, or None when staff chose no date."""
    if day is None:
        return None
    return dt.datetime.combine(day, dt.time.min, tzinfo=ZoneInfo(settings.BUSINESS_TIME_ZONE))


_MESSAGES = {
    "facts": _("Describe the facts relied on."),
    "ground": _("Give the legal ground or the term of the terms."),
    "exception": _("That reason for a shorter notice is not on the list."),
    "effective_at": _(
        "That date is too early: the notice is at least 30 days unless an exception applies."
    ),
    "vendor": _("Choose a vendor that is active or suspended."),
    "pending": _("This vendor already has a notice of termination."),
}


class TerminationListView(ManagerView):
    section = PLATFORM_SECTION
    template_name = "manager/platform/terminations.html"
    paginate_by = 25

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        rows = TerminationNotice.objects.for_actor(self.request.user).select_related("vendor")
        status = self.request.GET.get("status", "")
        if status in TerminationNotice.Status.values:
            rows = rows.filter(status=status)
        page = Paginator(rows, self.paginate_by).get_page(self.request.GET.get("page"))
        vendors = Vendor.objects.filter(status__in=services.TERMINABLE_STATUSES).order_by(
            "display_name", "id"
        )
        context.setdefault("form", TerminationForm(vendors=vendors))
        context.update(
            notices=page.object_list,
            page_obj=page,
            status=status,
            statuses=TerminationNotice.Status.choices,
            min_days=engine.MIN_NOTICE_DAYS,
        )
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        return requires_level(LEVEL_ACTION)(self._post)(request, *args, **kwargs)

    def _post(self, request: HttpRequest, /, *args: Any, **kwargs: Any) -> HttpResponse:
        if request.POST.get("action") != "issue":
            raise Http404("No such action.")
        vendors = Vendor.objects.filter(status__in=services.TERMINABLE_STATUSES)
        form = TerminationForm(request.POST, vendors=vendors)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form), status=200)
        data = form.cleaned_data
        try:
            changes = services.issue_termination_notice(
                request.user,
                data["vendor"],
                facts=data["facts"],
                ground=data["ground"],
                exception=data["exception"],
                effective_at=_effective_at(data["effective_on"]),
                ctx=audit_context(request),
            )
        except PermissionDenied:
            raise Http404("No such action.") from None
        except services.NoticeError as error:
            for code in error.codes:
                if code in form.fields:
                    form.add_error(code, _MESSAGES[code])
                else:
                    form.add_error(None, _MESSAGES.get(code, _("This notice is not possible.")))
            return self.render_to_response(self.get_context_data(form=form), status=200)
        messages.success(request, _("The notice is recorded and the vendor is told."))
        notice = changes.notice
        assert notice is not None  # noqa: S101  # a saved notice
        return HttpResponseRedirect(
            reverse("manager:platform:termination", args=[notice.public_id])
        )


class TerminationDetailView(ManagerView):
    section = PLATFORM_SECTION
    template_name = "manager/platform/termination_detail.html"

    def notice(self, public_id: uuid.UUID) -> TerminationNotice:
        try:
            return (
                TerminationNotice.objects.for_actor(self.request.user)
                .select_related("vendor")
                .get(public_id=public_id)
            )
        except TerminationNotice.DoesNotExist:
            raise Http404("No such notice.") from None

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context.setdefault("form", WithdrawTerminationForm())
        context.update(notice=self.notice(kwargs["public_id"]), title=_("Notice of termination"))
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        return requires_level(LEVEL_ACTION)(self._post)(request, *args, **kwargs)

    def _post(self, request: HttpRequest, /, *args: Any, **kwargs: Any) -> HttpResponse:
        public_id = kwargs["public_id"]
        self.notice(public_id)
        if request.POST.get("action") != "withdraw":
            raise Http404("No such action.")
        form = WithdrawTerminationForm(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form, **kwargs))
        try:
            services.withdraw_termination_notice(
                request.user,
                public_id,
                reason=form.cleaned_data["reason"],
                ctx=audit_context(request),
            )
        except PermissionDenied:
            raise Http404("No such action.") from None
        except services.NoticeError:
            messages.error(request, _("Only a pending notice can be withdrawn."))
        else:
            messages.success(request, _("The notice is withdrawn and the vendor is told."))
        return HttpResponseRedirect(reverse("manager:platform:termination", args=[public_id]))


# --- Shop space: the vendor during the notice period


class VendorTerminationView(ManagerView):
    """The reason, the dates and what the vendor can do meanwhile."""

    section = SHOP_SECTION
    template_name = "manager/shop/termination.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        vendor = self.manager.vendor
        context["notice"] = services.pending_termination(vendor) if vendor else None
        return context


class VendorDataDownloadView(ManagerView):
    """The vendor's own data as a JSON file. Not an `export` level action: it is the vendor's own
    data under the notice period (P2B art. 4), and vendor members cannot raise their session
    level."""

    section = SHOP_SECTION

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        vendor = self.manager.vendor
        if vendor is None:
            raise Http404("No vendor selected.")
        ctx = replace(audit_context(request), source=AuditLog.Source.VENDOR)
        bundle = services.vendor_data_bundle(vendor, ctx=ctx)
        response = HttpResponse(
            json.dumps(bundle, indent=2, ensure_ascii=False),
            content_type="application/json; charset=utf-8",
        )
        response["Content-Disposition"] = f'attachment; filename="{vendor.slug}-data.json"'
        response["Cache-Control"] = "no-store"
        return response
