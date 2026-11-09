"""Compliance data. GPSR product data and per-vendor defaults (CMP.3, spec 11).

`GpsrInfo` holds a product's own GPSR data (`product.gpsr`); `VendorGpsrDefaults` the vendor's
defaults, used for every product that leaves a block empty (the manufacturer often is the
vendor). The rules (what is mandatory, how defaults apply) are in `engine.py`; writes go through
the audited services in `services.py`.

GTIN and MPN stay on `catalog.Product` and count as product identifiers here; `GpsrInfo` adds the
model number and the batch. Both models are vendor-owned (CORE.3 scoped managers).
"""

from typing import ClassVar

from django.db import models

from apps.catalog.models import Product
from apps.core.fields import CountryField
from apps.core.models import BaseModel
from apps.vendors.models import Vendor
from apps.vendors.scoping import VendorScopedManager


class GpsrPartyFields(models.Model):
    """Manufacturer and EU responsible person (GPSR art. 16 and 19), all optional per row."""

    manufacturer_name = models.CharField(
        max_length=255, blank=True, help_text="Name, registered trade name or trade mark."
    )
    manufacturer_address = models.TextField("manufacturer postal address", blank=True)
    manufacturer_contact = models.CharField(
        "manufacturer electronic address",
        max_length=254,
        blank=True,
        help_text="Email or web address (https://) for safety questions.",
    )
    manufacturer_country = CountryField(
        blank=True,
        help_text="Where the manufacturer is established (ISO 3166-1 alpha-2). Outside the EU "
        "the EU responsible person is mandatory.",
    )
    eu_responsible_name = models.CharField("EU responsible person", max_length=255, blank=True)
    eu_responsible_address = models.TextField("EU responsible person postal address", blank=True)
    eu_responsible_contact = models.CharField(
        "EU responsible person electronic address", max_length=254, blank=True
    )

    class Meta:
        abstract = True


class VendorGpsrDefaults(GpsrPartyFields, BaseModel):
    """A vendor's default manufacturer and EU responsible person for its products."""

    vendor_scope: ClassVar[str] = "vendor"

    vendor = models.OneToOneField(Vendor, on_delete=models.CASCADE, related_name="gpsr_defaults")

    objects = VendorScopedManager()

    class Meta:
        verbose_name = "vendor GPSR defaults"
        verbose_name_plural = "vendor GPSR defaults"

    def __str__(self) -> str:
        return f"GPSR defaults of {self.vendor}"


class GpsrInfo(GpsrPartyFields, BaseModel):
    """A product's own GPSR data; an empty block falls back to the vendor defaults."""

    vendor_scope: ClassVar[str] = "product__vendor"

    product = models.OneToOneField(Product, on_delete=models.CASCADE, related_name="gpsr")
    model_number = models.CharField(
        "model or type", max_length=100, blank=True, help_text="Model, type or reference."
    )
    batch = models.CharField("batch or serial number", max_length=100, blank=True)

    objects = VendorScopedManager()

    class Meta:
        verbose_name = "GPSR information"
        verbose_name_plural = "GPSR information"

    def __str__(self) -> str:
        return f"GPSR of {self.product}"
