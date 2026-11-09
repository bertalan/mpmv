"""Compliance data. GPSR product data and per-vendor defaults (CMP.3, spec 11).

`GpsrInfo` holds a product's own GPSR data (`product.gpsr`); `VendorGpsrDefaults` the vendor's
defaults, used for every product that leaves a block empty (the manufacturer often is the
vendor). The rules (what is mandatory, how defaults apply) are in `engine.py`; writes go through
the audited services in `services.py`.

GTIN and MPN stay on `catalog.Product` and count as product identifiers here; `GpsrInfo` adds the
model number and the batch. `GpsrSafetyText` holds the warnings and safety information per
language; `SafetyCase` an authority order or a safety notice with its working-day deadline
(article 22). Every model is vendor-owned (CORE.3 scoped managers).
"""

from typing import ClassVar

from django.db import models

from apps.catalog.models import Product
from apps.core.fields import CountryField, LanguageField
from apps.core.models import BaseModel
from apps.vendors.models import Vendor
from apps.vendors.scoping import VendorScopedManager

from . import engine


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


class GpsrSafetyText(BaseModel):
    """A product's warnings and safety information in one language (GPSR art. 19(c)).

    Shown on the product page in the page language, else in the primary language. Optional:
    a product without warnings needs none.
    """

    vendor_scope: ClassVar[str] = "product__vendor"

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="gpsr_texts")
    language = LanguageField()
    warnings = models.TextField(blank=True, help_text="Warnings that accompany the product.")
    safety_information = models.TextField(
        blank=True, help_text="Safety information: use, assembly, maintenance, disposal."
    )

    objects = VendorScopedManager()

    class Meta:
        verbose_name = "GPSR warnings and safety information"
        verbose_name_plural = "GPSR warnings and safety information"
        constraints = [
            models.UniqueConstraint(
                fields=["product", "language"], name="compliance_gpsr_text_one_per_language"
            )
        ]

    def __str__(self) -> str:
        return f"GPSR texts of {self.product} ({self.language})"


class SafetyCase(BaseModel):
    """An authority order or a product safety notice the marketplace must act on (GPSR art. 22).

    `due_on` is computed when the case is opened (`engine.safety_case_due_on`) and kept: the
    deadline that applied never moves. `product_sku` keeps the SKU if the product is deleted.
    Written only through the audited services.
    """

    class Kind(models.TextChoices):
        AUTHORITY_ORDER = engine.AUTHORITY_ORDER, "Authority order (2 working days)"
        SAFETY_NOTICE = engine.SAFETY_NOTICE, "Product safety notice (3 working days)"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"

    vendor_scope: ClassVar[str] = "vendor"

    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name="safety_cases")
    product = models.ForeignKey(
        Product, null=True, blank=True, on_delete=models.SET_NULL, related_name="safety_cases"
    )
    product_sku = models.CharField(max_length=100, blank=True, editable=False)
    kind = models.CharField(max_length=20, choices=Kind.choices)
    reference = models.CharField(
        max_length=100, blank=True, help_text="The authority's or the notice's own reference."
    )
    authority = models.CharField(
        max_length=255, blank=True, help_text="The authority that issued the order."
    )
    summary = models.TextField(help_text="What is asked or reported.")
    received_at = models.DateTimeField()
    due_on = models.DateField(editable=False)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    closed_at = models.DateTimeField(null=True, blank=True, editable=False)
    resolution = models.TextField(blank=True, help_text="What was done.")
    overdue_alerted_at = models.DateTimeField(null=True, blank=True, editable=False)

    objects = VendorScopedManager()

    class Meta:
        ordering = ("due_on", "id")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status="open", closed_at__isnull=True)
                | models.Q(status="closed", closed_at__isnull=False),
                name="compliance_safety_case_closed_at_iff_closed",
            ),
            models.CheckConstraint(
                condition=models.Q(kind__in=[engine.AUTHORITY_ORDER, engine.SAFETY_NOTICE]),
                name="compliance_safety_case_kind_valid",
            ),
        ]
        indexes = [models.Index(fields=["status", "due_on"])]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} {self.reference or self.public_id}"
