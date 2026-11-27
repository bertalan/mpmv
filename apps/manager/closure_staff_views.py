"""Manager platform space: staff manage the closures of any shop (VEN.20 slice 3b).

Owner decision 2026-11-26: Support and the platform owner may list, add, edit and delete the
closures of any shop. The pages are the shop-space views with three things swapped: the shop comes
from the URL (an unknown id is a 404), the URLs point at the platform space, and the audit source
is `admin`. The services own every rule, so an ended range is as immutable for staff as for the
shop. Closures are everyday support work (CORE.25 group 1): no passkey step-up, the POST carries
the everyday session gate (`content_edit`, inherited from the shop views' POST methods, so a
policy that makes it sensitive gates staff too).
"""

import uuid
from typing import Any, Final

from django.core.paginator import Paginator
from django.db.models import QuerySet
from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from django.urls import reverse

from apps.core.models import AuditLog
from apps.vendors.models import Vendor, VendorClosure

from .closure_views import ClosureDeleteView, ClosureEditView, ClosuresView, _ClosureView
from .fitment_views import WriteGate
from .views import ManagerView

SECTION: Final = "platform.shop_closures"  # a row of roles.MATRIX
PICKER_URL: Final = "manager:platform:shop_closures"
PAGE_SIZE: Final = 50


class _StaffClosure(WriteGate, _ClosureView):
    """The shop-space behaviour, re-aimed at the shop named in the URL."""

    section = SECTION
    write_section = SECTION
    audit_source = AuditLog.Source.ADMIN
    _shop: Vendor

    def vendor(self) -> Vendor:
        """The shop of the URL, read once per request. A closed shop is a 404, as in the shop
        space (its closures no longer matter); suspended and onboarding shops stay editable."""
        if not hasattr(self, "_shop"):
            public_id: uuid.UUID = self.kwargs["vendor_id"]
            self._shop = get_object_or_404(
                Vendor.objects.exclude(status=Vendor.Status.CLOSED), public_id=public_id
            )
        return self._shop

    def rows(self) -> QuerySet[VendorClosure]:
        """Only the closures of the shop in the URL, so another shop's id is a 404."""
        return self.scoped(VendorClosure).filter(vendor=self.vendor())

    def list_url(self) -> str:
        return reverse("manager:platform:shop_closures_vendor", args=[self.vendor().public_id])

    def edit_url(self, closure: VendorClosure) -> str:
        return reverse(
            "manager:platform:shop_closure_edit", args=[self.vendor().public_id, closure.public_id]
        )

    def remove_url(self, closure: VendorClosure) -> str:
        return reverse(
            "manager:platform:shop_closure_remove",
            args=[self.vendor().public_id, closure.public_id],
        )


class StaffClosuresView(_StaffClosure, ClosuresView):
    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["shop_name"] = self.vendor().display_name
        context["picker_url"] = reverse(PICKER_URL)
        return context


class StaffClosureEditView(_StaffClosure, ClosureEditView):
    pass


class StaffClosureDeleteView(_StaffClosure, ClosureDeleteView):
    pass


class ShopPickerView(ManagerView):
    """Staff pick the shop whose closures they manage: a searchable list of every shop."""

    section = SECTION
    template_name = "manager/platform/shop_closures.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        request: HttpRequest = self.request
        query = request.GET.get("q", "").strip()[:100]
        shops = Vendor.objects.exclude(status=Vendor.Status.CLOSED).order_by("display_name", "id")
        if query:
            shops = shops.filter(display_name__icontains=query)
        page = Paginator(shops, PAGE_SIZE).get_page(request.GET.get("page"))
        context.update(
            query=query,
            page_obj=page,
            shops=[
                (shop, reverse("manager:platform:shop_closures_vendor", args=[shop.public_id]))
                for shop in page.object_list
            ],
        )
        return context
