"""Tenant (vendor) isolation: one mechanism for views, API, MCP, exports and admin search (CORE.3).

Spec 02 §2.2. Every model owned by a vendor (a ForeignKey chain to `Vendor`, and `Vendor`
itself) declares the path to its vendor and uses the scoped manager:

    class Product(BaseModel):
        vendor_scope: ClassVar[str] = "vendor"     # "pk" on Vendor, "order__vendor" via a parent
        vendor = models.ForeignKey(Vendor, ...)
        objects: ClassVar[VendorScopedManager["Product"]] = VendorScopedManager()

Then `Product.objects.for_member(request.user)` holds only the products of the user's vendors,
and `get_vendor_object_or_404(Product, request.user, public_id=...)` answers 404, never 403, for
another vendor's product, exactly as for a missing one, so callers cannot enumerate other
tenants. `tests.integration.test_tenant_isolation` fails when a vendor-owned model or an endpoint
is not covered.

Machine credentials act too (CORE.13): an API key bound to a vendor reaches only that vendor's
rows, a platform key (no vendor) every vendor's. API v1 operations and MCP tools pass the calling
client as the actor: `get_for_actor_or_404(Product, api_client_of(request), sku=sku)`.
"""

from typing import Any, Final, Protocol, Self

from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser, PermissionsMixin
from django.core.exceptions import FieldDoesNotExist, ObjectDoesNotExist, ValidationError
from django.db import models
from django.http import Http404

AnyUser = AbstractBaseUser | AnonymousUser


class Credential(Protocol):
    """A machine credential acting for itself, such as an API key (`apps.api.models.ApiClient`).

    `credential_vendor_id` is the pk of the vendor it is bound to, or None for a platform
    credential. Only active, authenticated credentials may be passed as actors.
    """

    @property
    def credential_vendor_id(self) -> int | None: ...


type Actor = AnyUser | Credential

# Class attribute of a vendor-owned model: the ORM lookup path from the model to its Vendor.
SCOPE_ATTRIBUTE: Final = "vendor_scope"


def scope_path(model: type[models.Model]) -> str:
    path = getattr(model, SCOPE_ATTRIBUTE, None)
    if not isinstance(path, str) or not path:
        raise TypeError(f"{model._meta.label} must declare `{SCOPE_ATTRIBUTE}`")
    return path


def scope_target(model: type[models.Model]) -> type[models.Model]:
    """The model that `vendor_scope` leads to (Vendor, for a correct declaration)."""
    current = model
    for name in scope_path(model).split("__"):
        if name == "pk":
            continue
        try:
            field = current._meta.get_field(name)
        except FieldDoesNotExist as exc:
            raise TypeError(f"{model._meta.label}.{SCOPE_ATTRIBUTE}: {exc}") from None
        if not isinstance(field, models.ForeignKey):  # OneToOneField included
            raise TypeError(f"{model._meta.label}.{SCOPE_ATTRIBUTE}: {name} is not a ForeignKey")
        current = field.related_model
    return current


PLATFORM_STAFF_PERMISSION: Final = "vendors.view_all_vendors"


def is_platform_staff(user: AnyUser) -> bool:
    """Platform staff (spec 02, Q-34): an active user holding `vendors.view_all_vendors`.

    `is_staff` (Wagtail or Django admin login) does not count: an editor sees no vendor data.
    The permission comes from a role group (CORE.18) or, for a superuser, from the superuser flag.
    """
    return bool(
        isinstance(user, PermissionsMixin)
        and user.is_active
        and user.has_perm(PLATFORM_STAFF_PERMISSION)
    )


class VendorScopedQuerySet[M: models.Model](models.QuerySet[M]):
    """A queryset that can be narrowed to one vendor, a vendor member, or any actor."""

    def _lookup(self) -> str:
        return scope_path(self.model)

    def for_vendor(self, vendor: models.Model) -> Self:
        """Only the rows owned by `vendor`."""
        return self.filter(**{self._lookup(): vendor.pk})

    def for_member(self, user: AnyUser) -> Self:
        """Only the rows of the vendors `user` is a member of (any role).

        Inactive and anonymous users get nothing. Platform staff are not members: staff pages use
        `for_actor`, or the staff admin. A credential is never a member: it goes through
        `for_actor`.
        """
        if not isinstance(user, AbstractBaseUser | AnonymousUser):
            raise TypeError("for_member takes a user; scope a credential with for_actor")
        if not user.is_authenticated or not user.is_active:
            return self.none()
        from .models import VendorMember  # models import this module

        vendors = VendorMember.objects.filter(user_id=user.pk).values("vendor_id")
        return self.filter(**{f"{self._lookup()}__in": vendors})

    def for_actor(self, actor: Actor) -> Self:
        """Spec 02 §2.2: platform staff see everything, members their own rows, others nothing.

        A credential (CORE.13) sees its vendor's rows, or every row when it is a platform one.
        """
        if not isinstance(actor, AbstractBaseUser | AnonymousUser):
            vendor_id = actor.credential_vendor_id
            return self.all() if vendor_id is None else self.filter(**{self._lookup(): vendor_id})
        if is_platform_staff(actor):
            return self.all()
        return self.for_member(actor)


# Default manager of every vendor-owned model; related managers (`vendor.members`) inherit it.
VendorScopedManager = models.Manager.from_queryset(VendorScopedQuerySet)


def is_vendor_scoped(model: type[models.Model]) -> bool:
    return isinstance(model._default_manager, VendorScopedManager)


type Source[M: models.Model] = type[M] | models.QuerySet[M] | models.Manager[M]


def _scoped[M: models.Model](source: Source[M]) -> VendorScopedQuerySet[M]:
    if isinstance(source, type):
        queryset: models.QuerySet[M] = source._default_manager.all()
    elif isinstance(source, models.Manager):
        queryset = source.all()
    else:
        queryset = source
    if not isinstance(queryset, VendorScopedQuerySet):
        raise TypeError(f"{queryset.model._meta.label} is not vendor-scoped")
    return queryset


def _get_or_404[M: models.Model](queryset: models.QuerySet[M], lookup: dict[str, Any]) -> M:
    try:
        return queryset.get(**lookup)
    except (ObjectDoesNotExist, ValidationError, ValueError):
        # One message for "not yours" and "does not exist": nothing tells them apart.
        raise Http404(f"No {queryset.model._meta.verbose_name} matches the given query.") from None


def get_vendor_object_or_404[M: models.Model](source: Source[M], user: AnyUser, **lookup: Any) -> M:
    """The object matching `lookup` among the rows of `user`'s vendors, else 404.

    For vendor-area views, the vendor API and MCP tools acting for a member. Another vendor's
    object, a missing object and a malformed lookup value all raise the same `Http404`.
    """
    return _get_or_404(_scoped(source).for_member(user), lookup)


def get_for_actor_or_404[M: models.Model](source: Source[M], actor: Actor, **lookup: Any) -> M:
    """Like `get_vendor_object_or_404`, but for any actor (`for_actor`).

    Platform staff and platform credentials reach every vendor's objects; a vendor credential
    (an API key bound to a vendor) only its own vendor's, with 404 for the others.
    """
    return _get_or_404(_scoped(source).for_actor(actor), lookup)
