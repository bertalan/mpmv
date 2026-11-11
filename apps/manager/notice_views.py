"""Manager platform space: notices of illegal content (DSA art. 16, CMP.5 part 2).

Thin adapters over `apps.compliance.services`. Notices are platform data, not vendor-owned, so
the vendor scoping of `ManagerView.scoped` does not apply: access is the section check
(`compliance.review_notices`, read from the permission tables). Staff see the notifier's details
here and only here; the audit log never holds them.
"""

import uuid
from typing import Any

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponseRedirect
from django.http.response import HttpResponseBase
from django.urls import reverse
from django.utils.translation import gettext as _

from apps.compliance import services
from apps.compliance.forms import AuthoritiesForm, DecisionForm, RestrictionForm
from apps.compliance.models import Notice, StatementOfReasons
from apps.core.audit import audit_context

from .navigation import platform_role
from .views import ManagerView

SECTION = "platform.notices"


def can_review(ctx: Any) -> bool:
    """The matrix row (CORE.18) and the permission the services check: both are needed."""
    return platform_role(SECTION)(ctx) and services.can_review_notices(ctx.user)


class NoticeListView(ManagerView):
    section = SECTION
    template_name = "manager/platform/notices.html"
    paginate_by = 25

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        rows = Notice.objects.all()
        status = self.request.GET.get("status", "")
        if status in Notice.Status.values:
            rows = rows.filter(status=status)
        page = Paginator(rows, self.paginate_by).get_page(self.request.GET.get("page"))
        context.update(
            notices=page.object_list,
            page_obj=page,
            status=status,
            statuses=Notice.Status.choices,
        )
        return context


class NoticeDetailView(ManagerView):
    section = SECTION
    template_name = "manager/platform/notice_detail.html"

    def notice(self, public_id: uuid.UUID) -> Notice:
        try:
            return Notice.objects.get(public_id=public_id)
        except Notice.DoesNotExist:
            raise Http404("No such notice.") from None

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        notice = self.notice(kwargs["public_id"])
        if self.request.method == "GET":
            services.record_notice_view(notice.public_id, ctx=audit_context(self.request))
        context.setdefault("decision_form", DecisionForm())
        context.setdefault(
            "restriction_form", RestrictionForm(initial={"target_kind": notice.target_kind})
        )
        context.setdefault("authorities_form", AuthoritiesForm())
        context.update(
            notice=notice,
            statements=StatementOfReasons.objects.filter(notice=notice),
            title=_("Notice"),
        )
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        public_id = kwargs["public_id"]
        self.notice(public_id)
        ctx = audit_context(request)
        action = request.POST.get("action")
        try:
            if action == "review":
                services.start_review(public_id, ctx=ctx)
                messages.success(request, _("The notice is under review."))
            elif action == "decide":
                form = DecisionForm(request.POST)
                if not form.is_valid():
                    return self.render_to_response(
                        self.get_context_data(decision_form=form, **kwargs)
                    )
                services.decide_notice(
                    public_id,
                    outcome=form.cleaned_data["outcome"],
                    reason=form.cleaned_data["reason"],
                    ctx=ctx,
                )
                messages.success(request, _("The decision is recorded and the notifier is told."))
            elif action == "restrict":
                restriction_form = RestrictionForm(request.POST)
                if not restriction_form.is_valid():
                    return self.render_to_response(
                        self.get_context_data(restriction_form=restriction_form, **kwargs)
                    )
                services.issue_statement(
                    request.user,
                    notice_id=public_id,
                    ctx=ctx,
                    **restriction_form.cleaned_data,
                )
                messages.success(request, _("The content is restricted and the vendor is told."))
            elif action == "authorities":
                authorities_form = AuthoritiesForm(request.POST)
                if not authorities_form.is_valid():
                    return self.render_to_response(
                        self.get_context_data(authorities_form=authorities_form, **kwargs)
                    )
                services.notify_authorities(
                    request.user, public_id, ctx=ctx, **authorities_form.cleaned_data
                )
                messages.success(request, _("The notification to the authorities is recorded."))
            else:
                raise Http404("No such action.")
        except PermissionDenied:
            raise Http404("No such action.") from None
        except services.NoticeError:
            if action == "restrict":
                messages.error(
                    request,
                    _("The content could not be restricted: check the notice status and target."),
                )
                return HttpResponseRedirect(reverse("manager:platform:notice", args=[public_id]))
            messages.error(request, _("This step is not possible for the notice in its status."))
        return HttpResponseRedirect(reverse("manager:platform:notice", args=[public_id]))
