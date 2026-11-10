"""Wagtail hooks of the vendors app (D-34): a vendor member sees only the vendor's own images and
documents in the choosers; staff and users with no vendor see everything, as before."""

from typing import Any

from django.db.models import Q, QuerySet
from wagtail import hooks
from wagtail.models import Collection

from .engine import vendor_collection_name
from .models import VendorMember


def _own_collections(user: Any) -> QuerySet[Collection] | None:
    """The collections (trees) of the user's vendors; None when nothing is restricted."""
    if not getattr(user, "is_authenticated", False) or user.is_staff or user.is_superuser:
        return None
    names = [
        vendor_collection_name(str(public_id))
        for public_id in VendorMember.objects.filter(user_id=user.pk).values_list(
            "vendor__public_id", flat=True
        )
    ]
    if not names:
        return None
    roots = Collection.objects.filter(depth=2, name__in=names)
    query = Q(pk__in=[])
    for root in roots:
        query |= Q(path__startswith=root.path)
    return Collection.objects.filter(query)  # type: ignore[no-any-return]


def _scoped(items: QuerySet[Any], request: Any) -> QuerySet[Any]:
    allowed = _own_collections(request.user)
    return items if allowed is None else items.filter(collection__in=allowed)


@hooks.register("construct_image_chooser_queryset")  # type: ignore[untyped-decorator]
def scope_image_chooser(images: QuerySet[Any], request: Any) -> QuerySet[Any]:
    return _scoped(images, request)


@hooks.register("construct_document_chooser_queryset")  # type: ignore[untyped-decorator]
def scope_document_chooser(documents: QuerySet[Any], request: Any) -> QuerySet[Any]:
    return _scoped(documents, request)
