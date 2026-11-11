"""Who may enter which Manager space, and the per-request actor (CORE.17, spec 02 §Backoffice).

- Shop space: vendor members (owner or staff, CORE.2), on one selected vendor.
- Platform space: platform staff, meaning a user who holds a dedicated platform permission
  (spec 02 Q-34). Wagtail or Django admin access (`is_staff`) never counts, and neither does
  superuser status (spec 02 CORE.18: the technical superuser holds no Manager section without a
  role). Such a user is the platform owner (`core.platform_owner`) or a member of a platform role
  group of `roles.py` (CORE.18).

Platform roles and vendor membership never mix (spec 02 Roles): a user who is both is let into
neither space, so a data error never opens a side door between them. A vendor member with admin
access (`is_staff` or superuser) is kept out of the shop space too: admin accounts stay on the
platform side.
"""

from dataclasses import dataclass, field

from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser
from django.urls import reverse

from apps.core.services import is_platform_owner
from apps.vendors.models import Vendor, VendorMember
from apps.vendors.services import is_vendor_member

from .role_groups import user_roles
from .spaces import Space

type AnyUser = AbstractBaseUser | AnonymousUser


def is_platform_staff(user: AnyUser) -> bool:
    """Holds a dedicated platform permission; `is_staff` and superuser status do not count."""
    if not (user.is_authenticated and user.is_active):
        return False
    return is_platform_owner(user) or bool(user_roles(user))


def _has_admin_access(user: AnyUser) -> bool:
    return bool(getattr(user, "is_staff", False) or getattr(user, "is_superuser", False))


def may_enter(space: Space, user: AnyUser) -> bool:
    """Whether `user` may enter `space` at all; the sections check the rest."""
    if not (user.is_authenticated and user.is_active):
        return False
    staff, member = is_platform_staff(user), is_vendor_member(user)
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


def home_url(user: AnyUser) -> str:
    """Where a user lands without `next`: their Manager space, else the site root."""
    for space in (Space.PLATFORM, Space.SHOP):
        if may_enter(space, user):
            return reverse(f"manager:{space}:dashboard")
    return "/"
