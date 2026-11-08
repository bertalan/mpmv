"""Re-check a vendor's tax profile when its VAT number is saved (TAX.13, owner decision MQ-3).

An exemption reason is for VAT numbers of the country whose law defines it. The VAT number
lives on the vendor (VEN.12) and the vendor app cannot import tax, so every save that writes
the VAT number, whatever the adapter (admin, import, API), runs the consistency check here and
logs what no longer matches; the nightly job and the tax admin list it too.
"""

from typing import Any

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.vendors.models import Vendor

from .services import report_vendor_tax_profile


@receiver(post_save, sender=Vendor, dispatch_uid="tax_vat_number_recheck")
def recheck_on_vat_number_save(
    sender: Any, instance: Vendor, created: bool, update_fields: Any, **kwargs: Any
) -> None:
    if created or (update_fields is not None and "vat_number" not in update_fields):
        return
    report_vendor_tax_profile(instance)
