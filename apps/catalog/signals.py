"""Guards on catalogue relations that the database cannot express (CAT.14, Q-32).

A suggestion (`Product.upsells`) to another vendor's product needs that vendor's opt-in, a
versioned setting in another table: every add, from either side of the relation and whatever
the adapter (admin, import, API), goes through `services.check_upsells`.
"""

from typing import Any

from django.db.models.signals import m2m_changed
from django.dispatch import receiver

from .models import Product
from .services import check_upsells


@receiver(m2m_changed, sender=Product.upsells.through, dispatch_uid="catalog_upsells_guard")
def guard_upsells(
    sender: Any, instance: Product, action: str, reverse: bool, pk_set: set[int] | None, **kw: Any
) -> None:
    if action != "pre_add" or not pk_set:
        return
    if reverse:  # `target.upsold_by.add(*sources)`
        sources = set(Product.objects.filter(pk__in=pk_set).values_list("vendor_id", flat=True))
        check_upsells(sources, [instance.pk])
    else:
        check_upsells({instance.vendor_id}, pk_set)
