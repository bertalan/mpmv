"""Compliance data. GPSR product data and per-vendor defaults (CMP.3, spec 11).

`GpsrInfo` holds a product's own GPSR data (`product.gpsr`); `VendorGpsrDefaults` the vendor's
defaults, used for every product that leaves a block empty (the manufacturer often is the
vendor). The rules (what is mandatory, how defaults apply) are in `engine.py`; writes go through
the audited services in `services.py`.

GTIN and MPN stay on `catalog.Product` and count as product identifiers here; `GpsrInfo` adds the
model number and the batch. `GpsrSafetyText` holds the warnings and safety information per
language; `SafetyCase` an authority order or a safety notice with its working-day deadline
(article 22). Every model is vendor-owned (CORE.3 scoped managers), except
`CountryWarningLanguages`: the platform's map of the languages each destination country accepts
for warnings, edited by staff.
"""

from typing import ClassVar

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils import timezone

from apps.catalog.models import Product
from apps.core.fields import CountryField, EncryptedCharField, LanguageField
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


class CountryWarningLanguages(BaseModel):
    """The languages (ISO 639-1) in which a destination country accepts a product's warnings
    (GPSR art. 19(c), CMP.3). Platform data, not vendor-owned.

    Seeded from the official languages of the EU and EEA states (`apps.core.countries`) and
    edited by staff through `services.set_country_warning_languages` (validated, audited). A
    country without a row uses its official languages; a country outside both is not checked.
    """

    country = CountryField(unique=True)
    languages = ArrayField(
        models.CharField(max_length=3),
        help_text="ISO 639-1 codes; warnings in any one of them are enough.",
    )

    class Meta:
        ordering = ("country",)
        verbose_name = "warnings languages of a country"
        verbose_name_plural = "warnings languages per country"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(languages__len__gt=0),
                name="compliance_country_languages_not_empty",
            )
        ]

    def __str__(self) -> str:
        return f"{self.country}: {', '.join(self.languages)}"


class Notice(BaseModel):
    """A report of illegal content under DSA article 16 (CMP.5 part 2).

    Platform-level data, not vendor-owned: a notice may be about a product, an event, a vendor
    storefront or other content, and anyone may send one without an account. `url` is the exact
    location the notifier gave. The notifier's name and email are encrypted at rest and never
    copied to the audit log; both stay empty for the anonymous category (art. 16(2)(c)).
    Written only through the audited services in `services.py`.
    """

    class Status(models.TextChoices):
        RECEIVED = engine.NOTICE_RECEIVED, "Received"
        UNDER_REVIEW = engine.NOTICE_UNDER_REVIEW, "Under review"
        ACTION_TAKEN = engine.NOTICE_ACTION_TAKEN, "Action taken"
        NO_ACTION = engine.NOTICE_NO_ACTION, "No action"

    class TargetKind(models.TextChoices):
        PRODUCT = "product", "Product"
        EVENT = "event", "Event"
        VENDOR = "vendor", "Vendor storefront"
        CONTENT = "content", "Other site content"

    class Category(models.TextChoices):
        GENERAL = "general", "Illegal content"
        CHILD_ABUSE = engine.NOTICE_ANONYMOUS_CATEGORY, "Child sexual abuse material"

    target_kind = models.CharField(max_length=10, choices=TargetKind.choices)
    url = models.URLField(max_length=2000, help_text="The exact location of the content.")
    category = models.CharField(max_length=12, choices=Category.choices, default=Category.GENERAL)
    explanation = models.TextField(help_text="Why the notifier considers the content illegal.")
    notifier_name = EncryptedCharField(max_length=255, blank=True)
    notifier_email = EncryptedCharField(max_length=254, blank=True)
    notifier_language = LanguageField(blank=True)
    good_faith = models.BooleanField(
        help_text="The notifier stated that the notice is accurate and complete."
    )
    status = models.CharField(max_length=14, choices=Status.choices, default=Status.RECEIVED)
    decision_reason = models.TextField(blank=True)
    decided_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ("-created_at", "-id")
        permissions = [("review_notices", "Review notices of illegal content (DSA art. 16)")]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(good_faith=True), name="compliance_notice_good_faith"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=["action_taken", "no_action"], decided_at__isnull=False
                )
                | models.Q(status__in=["received", "under_review"], decided_at__isnull=True),
                name="compliance_notice_decided_at_iff_decided",
            ),
        ]

    def __str__(self) -> str:
        return f"Notice {self.public_id} ({self.status})"


class ErasureRequest(BaseModel):
    """A customer's account erasure (CMP.4, D-30): accepted at once, complete when the profile is
    anonymised, which waits for the last open order. Holds no personal data; the account is
    reached through the user (the account row stays, anonymised)."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="erasure_request"
    )
    requested_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["requested_at", "id"]

    def __str__(self) -> str:
        return f"erasure of user {self.user_id} ({'done' if self.completed_at else 'pending'})"
