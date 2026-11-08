"""Re-check a vendor's tax profile when its VAT country changes (TAX.13, owner decision MQ-3).

An exemption reason is for VAT numbers of the country whose law defines it. The VAT number
lives on the vendor (VEN.12) and the vendor app cannot import tax, so every save that changes
the VAT number or the country, whatever the adapter (admin, import, API), runs the consistency
check here and logs what no longer matches; the nightly job and the tax admin list it too.

Only a real change of those fields triggers the check: saving an already flagged vendor for
another reason does not log it again. The check runs after the commit, so a regime change
approval (which saves the vendor before adding the new profile) is compared with the new
profile, and a rolled-back save logs nothing.
"""

from functools import partial
from typing import Any

from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from apps.vendors.models import Vendor

from .services import report_vendor_tax_profile

VAT_FIELDS = ("vat_number", "country")
_CHANGED = "_tax_vat_country_changed"


@receiver(pre_save, sender=Vendor, dispatch_uid="tax_vat_number_snapshot")
def note_vat_change(sender: Any, instance: Vendor, update_fields: Any, **kwargs: Any) -> None:
    saved = [name for name in VAT_FIELDS if update_fields is None or name in update_fields]
    changed = False
    if saved and instance.pk is not None and not instance._state.adding:
        stored = Vendor.objects.filter(pk=instance.pk).values(*saved).first()
        changed = stored is not None and any(
            stored[name] != getattr(instance, name) for name in saved
        )
    setattr(instance, _CHANGED, changed)


@receiver(post_save, sender=Vendor, dispatch_uid="tax_vat_number_recheck")
def recheck_on_vat_change(sender: Any, instance: Vendor, created: bool, **kwargs: Any) -> None:
    if created or not getattr(instance, _CHANGED, False):
        return
    setattr(instance, _CHANGED, False)
    transaction.on_commit(partial(_recheck, instance.pk))


def _recheck(vendor_pk: int) -> None:
    vendor = Vendor.objects.filter(pk=vendor_pk).first()
    if vendor is not None:
        report_vendor_tax_profile(vendor)
