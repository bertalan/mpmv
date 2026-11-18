"""The staff review list of orphan images in the Wagtail admin (CAT.25).

GET lists the library images nothing uses. POST works on the ticked images: "preview" shows what
a delete would remove (a dry run), "delete" removes them. Both need Wagtail's image delete
permission; the delete also passes the session gate (a sensitive action: the passkey window).
"""

from typing import Any

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views import View

from apps.accounts.decorators import requires_level_method
from apps.core.audit import audit_context

from . import media_cleanup
from .media_cleanup import image_policy

GATE_ACTION = "bulk_edit"  # deleting many images at once: a sensitive session level (CORE.25)
TEMPLATE = "catalog/admin/orphan_images.html"


def can_review_orphans(request: HttpRequest) -> bool:
    return bool(image_policy().user_has_permission(request.user, "delete"))


class OrphanImagesView(View):
    def _render(
        self, request: HttpRequest, *, preview: Any = None, status: int = 200
    ) -> TemplateResponse:
        report = media_cleanup.orphan_images(
            request.user, page=request.GET.get("p", 1), per_page=media_cleanup.REVIEW_LIMIT
        )
        response = TemplateResponse(
            request,
            TEMPLATE,
            {
                "page_title": _("Unused images"),
                "header_icon": "image",
                "images": report.images,
                "total": report.total,
                "page_obj": report.page,
                "elided_page_range": list(
                    report.page.paginator.get_elided_page_range(report.page.number)
                ),
                "preview": preview,
            },
            status=status,
        )
        return response

    def get(self, request: HttpRequest) -> HttpResponse:
        if not can_review_orphans(request):
            raise PermissionDenied
        return self._render(request)

    @requires_level_method(GATE_ACTION)
    def post(self, request: HttpRequest) -> HttpResponse:
        if not can_review_orphans(request):
            raise PermissionDenied
        try:
            ids = [int(value) for value in request.POST.getlist("image")]
        except ValueError:
            ids = []
        if not ids:
            messages.error(request, _("Tick at least one image."))
            return redirect(reverse("catalog_orphan_images"))
        dry_run = request.POST.get("mode") != "delete"
        result = media_cleanup.delete_orphans(
            ids, user=request.user, ctx=audit_context(request), dry_run=dry_run
        )
        if dry_run:
            return self._render(request, preview=result)
        if result.deleted:
            messages.success(
                request,
                ngettext(
                    "%(count)d image deleted.", "%(count)d images deleted.", len(result.deleted)
                )
                % {"count": len(result.deleted)},
            )
        if result.refused:
            messages.warning(
                request,
                ngettext(
                    "%(count)d image was kept: it is in use again, too new or not yours to delete.",
                    "%(count)d images were kept: they are in use again, too new or not yours "
                    "to delete.",
                    len(result.refused),
                )
                % {"count": len(result.refused)},
            )
        return redirect(reverse("catalog_orphan_images"))
