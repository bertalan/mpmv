"""The staff screen for the image renditions in the Wagtail admin (CAT.24).

GET lists the images that still lack renditions and the failed render jobs (with the last error);
POST queues the job of one image and group again. Both need Wagtail's image change permission;
the POST also passes the session gate (a sensitive action: the passkey window).
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views import View

from apps.accounts.decorators import requires_level_method
from apps.core import rendition_monitor
from apps.core.audit import audit_context

from .media_cleanup import image_policy

GATE_ACTION = "bulk_edit"
TEMPLATE = "catalog/admin/rendition_monitor.html"
PAGE_SIZE = 25


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
            changes = rendition_monitor.retry_render(
                image_id, request.POST.get("group", ""), ctx=audit_context(request)
            )
        except (ValueError, LookupError):
            messages.error(request, _("That image or group is not known."))
        else:
            if changes.is_empty:
                messages.info(request, _("A job for this image and group is already waiting."))
            else:
                messages.success(request, _("The job is queued again."))
        return redirect(reverse("catalog_rendition_monitor"))
