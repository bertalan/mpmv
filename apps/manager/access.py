"""Who may enter which Manager space, and the per-request actor (CORE.17, spec 02 §Backoffice).

- Shop space: vendor members (owner or staff, CORE.2), on one selected vendor.
- Platform space: platform staff, as defined by `apps.vendors.scoping.is_platform_staff()`: an
  active user holding `vendors.view_all_vendors` (spec 02 Q-34). Wagtail or Django admin access
  (`is_staff`) neither grants nor is needed for it. A superuser passes only with the permission
  granted explicitly, not through Django's superuser rule (spec 02 CORE.18: the technical
  superuser holds no Manager section without a role). CORE.18 adds the per-section role checks.

Platform roles and vendor membership never mix (spec 02 Roles): a user who is both is let into
neither space, so a data error never opens a side door between them. A vendor member with admin
access (`is_staff` or superuser) is kept out of the shop space too: admin accounts stay on the
platform side.
"""

from dataclasses import dataclass, field

from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser, Permission
from django.db.models import Q

from apps.vendors.models import Vendor, VendorMember
from apps.vendors.scoping import PLATFORM_STAFF_PERMISSION
from apps.vendors.scoping import is_platform_staff as holds_staff_permission
from apps.vendors.services import is_vendor_member

from .spaces import Space

type AnyUser = AbstractBaseUser | AnonymousUser


def may_enter_platform_space(user: AnyUser) -> bool:
    """`vendors.scoping.is_platform_staff()`, minus the implicit superuser pass.

    `is_staff` does not count. A superuser needs the permission granted to them or to one of
    their groups: Django's `has_perm` would grant it to every superuser.
    """
    if not (user.is_authenticated and user.is_active and holds_staff_permission(user)):
        return False
    if not getattr(user, "is_superuser", False):
        return True
    app_label, codename = PLATFORM_STAFF_PERMISSION.split(".")
    return Permission.objects.filter(
        Q(user=user) | Q(group__user=user),
        content_type__app_label=app_label,
        codename=codename,
    ).exists()


def _has_admin_access(user: AnyUser) -> bool:
    return bool(getattr(user, "is_staff", False) or getattr(user, "is_superuser", False))


def may_enter(space: Space, user: AnyUser) -> bool:
    """Whether `user` may enter `space` at all; the sections check the rest."""
    if not (user.is_authenticated and user.is_active):
        return False
    staff, member = may_enter_platform_space(user), is_vendor_member(user)
    if space is Space.SHOP:
        return member and not staff and not _has_admin_access(user)
    return staff and not member


@dataclass(frozen=True)
class ManagerContext:
    """The actor of one Manager request, set by the gate as `request.manager`.

    In the shop space `vendor` is the selected vendor (None only on the vendor picker), `role`
    the user's role there and `memberships` all of the user's memberships, for the switcher.
    """

    user: AbstractBaseUser
    space: Space
    vendor: Vendor | None = None
    role: str | None = None
    memberships: tuple[VendorMember, ...] = field(default=())
