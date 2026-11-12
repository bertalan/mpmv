"""Manager: the legacy store reviews and enquiries export (MIG.21, MIG.18 in lane-a's wording).

Thin adapters over `apps.vendors.services.build_legacy_export`. In the shop space an owner
downloads their own vendor's file: the vendor is always the selected one, and a request that names
another vendor (any of `vendor`, `vendor_id`, `id`) answers 404, like a missing one, and is
audited. In the platform space support and the platform owner generate a vendor's file on its
behalf; every download is audited. The file is never cached.
"""

from typing import Any

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.cache import add_never_cache_headers

from apps.core.audit import audit_context
from apps.vendors import services
from apps.vendors.models import Vendor

from . import roles
from .role_groups import user_roles
from .views import ManagerView

SHOP_SECTION = "shop.legacy_export"
PLATFORM_SECTION = "platform.vendor_legacy_export"
TAMPERING_PARAMS = ("vendor", "vendor_id", "id")


def can_export_for_vendor(ctx: Any) -> bool:
    return roles.allows(PLATFORM_SECTION, user_roles(ctx.user), write=True)


def csv_response(file: services.LegacyExportFile) -> HttpResponse:
    response = HttpResponse(file.content, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{file.filename}"'
    response["X-Content-Type-Options"] = "nosniff"
    add_never_cache_headers(response)
    response["Cache-Control"] = "no-store, private, max-age=0"
    return response


class ShopLegacyExportView(ManagerView):
    section = SHOP_SECTION
    http_method_names = ["get", "head"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        vendor = self.manager.vendor
        if vendor is None:  # pragma: no cover - the section needs a vendor
            raise Http404("No Manager page here.")
        ctx = audit_context(request)
        for name in TAMPERING_PARAMS:
            named = request.GET.get(name)
            if named is not None and named != str(vendor.public_id):
                services.refuse_legacy_export(vendor, ctx=ctx, requested=named)
                raise Http404("No such vendor.")
        return csv_response(services.build_legacy_export(vendor, ctx=ctx))


class PlatformLegacyExportView(ManagerView):
    section = PLATFORM_SECTION
    http_method_names = ["get", "head"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        vendor = get_object_or_404(Vendor, public_id=kwargs["vendor_public_id"])
        file = services.build_legacy_export(vendor, ctx=audit_context(request), on_behalf=True)
        return csv_response(file)
