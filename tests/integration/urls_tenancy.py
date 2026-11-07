"""Test-only URLconf for the cross-tenant harness (CORE.3).

Correct views reach vendor data through `get_vendor_object_or_404`; the `leaky_*` and `wrong_*`
views make the classic mistakes, so the tests can prove that the harness catches each one.
"""

import uuid

from django.contrib.auth.decorators import user_passes_test
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.urls import path

from apps.vendors.models import Vendor, VendorMember, VendorTranslation
from apps.vendors.scoping import get_vendor_object_or_404


def member_detail(request: HttpRequest, public_id: uuid.UUID) -> JsonResponse:
    member = get_vendor_object_or_404(VendorMember, request.user, public_id=public_id)
    return JsonResponse({"user": member.user.email, "vendor": member.vendor.legal_name})


def translation_update(request: HttpRequest, public_id: uuid.UUID) -> JsonResponse:
    translation = get_vendor_object_or_404(VendorTranslation, request.user, public_id=public_id)
    if request.method == "POST":
        translation.tagline = "Changed"
        translation.save(update_fields=["tagline", "updated_at"])
    return JsonResponse({"tagline": translation.tagline})


def leaky_member_detail(request: HttpRequest, public_id: uuid.UUID) -> JsonResponse:
    member = VendorMember.objects.get(public_id=public_id)  # no tenant filter
    return JsonResponse({"user": member.user.email})


def forbidden_member_detail(request: HttpRequest, public_id: uuid.UUID) -> HttpResponse:
    member = VendorMember.objects.get(public_id=public_id)
    if not member.vendor.members.filter(user__email=getattr(request.user, "email", "")).exists():
        return HttpResponse(status=403)  # tells the caller that the object exists
    return JsonResponse({"user": member.user.email})


def leaky_translation_update(request: HttpRequest, public_id: uuid.UUID) -> HttpResponse:
    translation = VendorTranslation.objects.get(public_id=public_id)
    translation.tagline = "Changed"
    translation.save(update_fields=["tagline", "updated_at"])  # changed before the check
    get_vendor_object_or_404(VendorTranslation, request.user, public_id=public_id)
    return JsonResponse({"tagline": translation.tagline})


def wrong_member_detail(request: HttpRequest, public_id: uuid.UUID) -> HttpResponse:
    raise Http404  # never finds anything: a cross-tenant 404 from it proves nothing


@user_passes_test(lambda user: user.is_staff, login_url="/login/")
def staff_vendor_detail(request: HttpRequest, object_id: str) -> HttpResponse:
    vendor = Vendor.objects.filter(pk=object_id).first()
    if vendor is None:
        raise Http404
    return HttpResponse(vendor.legal_name)


def leaky_staff_vendor_detail(request: HttpRequest, object_id: str) -> HttpResponse:
    vendor = Vendor.objects.get(pk=object_id)  # forgot the staff check
    return HttpResponse(vendor.legal_name)


def every_vendor(request: HttpRequest) -> HttpResponse:
    return HttpResponse(", ".join(Vendor.objects.values_list("legal_name", flat=True)))


def own_profile(request: HttpRequest) -> HttpResponse:
    return HttpResponse(
        ", ".join(Vendor.objects.for_member(request.user).values_list("legal_name", flat=True))
    )


urlpatterns = [
    path("members/<uuid:public_id>/", member_detail, name="member"),
    path("translations/<uuid:public_id>/", translation_update, name="translation"),
    path("leaky/members/<uuid:public_id>/", leaky_member_detail, name="leaky-member"),
    path("forbidden/members/<uuid:public_id>/", forbidden_member_detail, name="forbidden-member"),
    path(
        "leaky/translations/<uuid:public_id>/",
        leaky_translation_update,
        name="leaky-translation",
    ),
    path("wrong/members/<uuid:public_id>/", wrong_member_detail, name="wrong-member"),
    path("staff/vendors/<str:object_id>/", staff_vendor_detail, name="staff-vendor"),
    path("leaky/staff/vendors/<str:object_id>/", leaky_staff_vendor_detail, name="leaky-staff"),
    path("vendors/", every_vendor, name="every-vendor"),
    path("profile/", own_profile, name="own-profile"),
]
