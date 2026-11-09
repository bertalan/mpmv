"""Test-only URLconf for the endpoint security sweep (SEC.5).

The site's routes plus two detail pages of a vendor: `unprotected_vendor` forgot to require
sign-in, `protected_vendor` is the same view behind the staff check. The sweep must fail on the
first and pass on the second (`test_the_sweep_fails_on_an_unprotected_detail_view`).
"""

import uuid

from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import path

from apps.vendors.models import Vendor
from mpmv.urls import urlpatterns as site_urlpatterns


def vendor_detail(request: HttpRequest, public_id: uuid.UUID) -> HttpResponse:
    vendor = get_object_or_404(Vendor, public_id=public_id)  # no sign-in, no tenant filter
    return HttpResponse(vendor.legal_name)


urlpatterns = [
    path("unprotected/vendors/<uuid:public_id>/", vendor_detail, name="unprotected_vendor"),
    path(
        "protected/vendors/<uuid:public_id>/",
        staff_member_required(vendor_detail),
        name="protected_vendor",
    ),
    *site_urlpatterns,
]
