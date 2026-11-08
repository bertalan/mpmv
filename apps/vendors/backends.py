"""Authorization backend of the vendors app (spec 02, spec 15 "How vendors author an `EventPage`").

Vendors do not get the Wagtail admin: vendor owners and vendor staff use the vendor area. The
admin's explorer, search, revisions, reports and bulk actions are not tenant-scoped, so a vendor
member who is not platform staff is refused Wagtail's `access_admin` permission even when an
editor group grants it. Listed in `AUTHENTICATION_BACKENDS` before `ModelBackend`: raising
`PermissionDenied` in `has_perm` stops Django from asking the later backends. Active superusers
never reach a backend.
"""

from typing import Any

from django.contrib.auth.backends import BaseBackend
from django.core.exceptions import PermissionDenied

from .models import VendorMember

WAGTAIL_ADMIN_ACCESS = "wagtailadmin.access_admin"


class VendorAdminRefusalBackend(BaseBackend):
    """Refuse the Wagtail admin to vendor members who are not staff. Authenticates no one."""

    def authenticate(self, request: Any, **credentials: Any) -> None:
        return None

    def has_perm(self, user_obj: Any, perm: str, obj: Any = None) -> bool:
        if perm != WAGTAIL_ADMIN_ACCESS or not getattr(user_obj, "is_authenticated", False):
            return False
        if getattr(user_obj, "is_staff", False):
            return False
        cached: bool | None = getattr(user_obj, "_mpmv_is_vendor_member", None)
        if cached is None:
            cached = VendorMember.objects.filter(user_id=user_obj.pk).exists()
            user_obj._mpmv_is_vendor_member = cached
        if cached:
            raise PermissionDenied
        return False
