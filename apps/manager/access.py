"""Who may enter which Manager space, and the per-request actor (CORE.17, spec 02 §Backoffice).

- Shop space: vendor members (owner or staff, CORE.2), on one selected vendor.
- Platform space: platform staff. Until CORE.18 adds the staff roles, platform staff means what
  tenant scoping already counts as platform staff (`is_staff`, superusers included, spec 02
  "Tenant (vendor) isolation"), plus the platform owner permission (`core.platform_owner`).
  CORE.18 replaces `is_platform_staff` with the role membership of `roles.py`.

Platform roles and vendor membership never mix (spec 02 Roles): a user who is both is let into
neither space, so a data error never opens a side door between them.
"""

from dataclasses import dataclass, field

from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser

from apps.core.services import is_platform_owner
from apps.vendors.models import Vendor, VendorMember
from apps.vendors.services import is_vendor_member

from .spaces import Space

type AnyUser = AbstractBaseUser | AnonymousUser


def is_platform_staff(user: AnyUser) -> bool:
    if not (user.is_authenticated and user.is_active):
        return False
    return bool(getattr(user, "is_staff", False)) or is_platform_owner(user)


def may_enter(space: Space, user: AnyUser) -> bool:
    """Whether `user` may enter `space` at all; the sections check the rest."""
    if not (user.is_authenticated and user.is_active):
        return False
    staff, member = is_platform_staff(user), is_vendor_member(user)
    if space is Space.SHOP:
        return member and not staff
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
