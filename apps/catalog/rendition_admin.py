"""The staff screen for the image renditions in the Wagtail admin (CAT.24).

GET lists the images that still lack renditions and the failed render jobs (with the last error);
POST queues the job of one image and group again. Both need Wagtail's image change permission;
the POST also passes the session gate (a sensitive action: the passkey window).
"""

import datetime as dt

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views import View

from apps.accounts.decorators import requires_level_method
from apps.core import rendition_monitor
from apps.core.audit import audit_context
from apps.core.services import consume_rate_limit

from .media_cleanup import image_policy

GATE_ACTION = "bulk_edit"
TEMPLATE = "catalog/admin/rendition_monitor.html"
PAGE_SIZE = 25
RETRY_LIMIT = 20  # retries per user per window
RETRY_WINDOW = dt.timedelta(minutes=1)


def can_monitor_renditions(request: HttpRequest) -> bool:
    return bool(image_policy().user_has_permission(request.user, "change"))


class RenditionMonitorView(View):
    def get(self, request: HttpRequest) -> HttpResponse:
        if not can_monitor_renditions(request):
            raise PermissionDenied
        report = rendition_monitor.images_missing_renditions()
        page = Paginator(report.rows, PAGE_SIZE).get_page(request.GET.get("page"))
        return TemplateResponse(
            request,
            TEMPLATE,
            {
                "page_title": _("Image renditions"),
                "header_icon": "image",
                "failed": rendition_monitor.failed_jobs(),
                "missing": list(page),
                "page_obj": page,
                "report": report,
            },
        )

    @requires_level_method(GATE_ACTION)
    def post(self, request: HttpRequest) -> HttpResponse:
        if not can_monitor_renditions(request):
            raise PermissionDenied
        try:
            image_id = int(request.POST.get("image", ""))
        except ValueError:
            return HttpResponseBadRequest(_("That image or group is not known."))
        wait = consume_rate_limit(
            f"catalog:rendition_retry:{request.user.pk}", limit=RETRY_LIMIT, window=RETRY_WINDOW
        )
        if wait:
            response = HttpResponse(_("Too many retries. Try again in a minute."), status=429)
            response["Retry-After"] = str(int(wait.total_seconds()) + 1)
            return response
        try:
            changes = rendition_monitor.retry_render(
                image_id, request.POST.get("group", ""), ctx=audit_context(request)
            )
        except rendition_monitor.NotListed as error:
            raise Http404(str(error)) from error
        except ValueError:
            return HttpResponseBadRequest(_("That image or group is not known."))
        if changes.is_empty:
            messages.info(request, _("A job for this image and group is already waiting."))
        else:
            messages.success(request, _("The job is queued again."))
        return redirect(reverse("catalog_rendition_monitor"))
