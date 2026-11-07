"""Vendors and their per-language storefront texts and policies (spec 04 §4.2, VEN.1).

Fields owned by later tasks are added there: `plan` (VEN.2), `payout_account` (VEN.6),
`dac7_complete` (CMP.1), `logo` (media/images). Storefront texts follow the shared translation
workflow (`apps.core.models.TranslationModel`, I18N.2).
"""

from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.fields import CountryField
from apps.core.models import BaseModel, TranslationModel, resolve_translation
from apps.core.translations import Resolved

from . import engine


def validate_registered_address(value: Any) -> None:
    errors = engine.address_errors(value)
    if errors:
        raise ValidationError(errors, code="invalid_address")


class VendorStatus(models.TextChoices):
    APPLICANT = engine.APPLICANT, "Applicant"
    ONBOARDING = engine.ONBOARDING, "Onboarding"
    ACTIVE = engine.ACTIVE, "Active"
    SUSPENDED = engine.SUSPENDED, "Suspended"
    CLOSED = engine.CLOSED, "Closed"


class VendorSalesMode(models.TextChoices):
    BUY = "buy", "Buy"
    ON_REQUEST = "on_request", "On request"


class Vendor(BaseModel):
    Status = VendorStatus
    SalesMode = VendorSalesMode

    legal_name = models.CharField(max_length=200)
    display_name = models.CharField(max_length=150)
    slug = models.SlugField(
        max_length=engine.SLUG_MAX_LENGTH,
        unique=True,
        blank=True,
        help_text="Leave empty to generate it from the display name. Changing it breaks URLs.",
    )
    # Changed only through `services.change_status`, which checks the state machine and audits.
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.APPLICANT, editable=False
    )
    country = CountryField()
    vat_number = models.CharField(max_length=32, blank=True)
    tax_code = models.CharField(max_length=32, blank=True)
    registered_address = models.JSONField(
        default=dict,
        blank=True,
        validators=[validate_registered_address],
        help_text="Keys: " + ", ".join(engine.ADDRESS_FIELDS) + ".",
    )
    contact_email = models.EmailField()
    phone = models.CharField(max_length=32, blank=True)
    sales_mode_default = models.CharField(
        max_length=16, choices=SalesMode.choices, default=SalesMode.BUY
    )
    enquiries_enabled = models.BooleanField(default=False)
    legacy_wp_user_id = models.PositiveBigIntegerField(
        null=True, blank=True, unique=True, help_text="Vendor user id in the legacy system."
    )

    class Meta:
        ordering = ["display_name", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=VendorStatus.values),
                name="vendors_vendor_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(sales_mode_default__in=VendorSalesMode.values),
                name="vendors_vendor_sales_mode_valid",
            ),
        ]

    def __str__(self) -> str:
        return self.display_name

    def translation(self, language: str) -> "Resolved[VendorTranslation]":
        """Storefront texts to show in `language`: published, or EN with a fallback flag."""
        return resolve_translation(self.translations.all(), language)

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.slug:
            self.slug = engine.unique_slug(self.display_name, taken=_TakenSlugs(self.pk))
        if self.registered_address is None:  # a cleared form field gives None; column is NOT NULL
            self.registered_address = {}
        super().save(*args, **kwargs)


class _TakenSlugs:
    """`slug in taken` asks the database, so `engine.unique_slug` stays pure."""

    def __init__(self, exclude_pk: int | None) -> None:
        self.exclude_pk = exclude_pk

    def __contains__(self, slug: object) -> bool:
        others = Vendor.objects.all()
        if self.exclude_pk is not None:
            others = others.exclude(pk=self.exclude_pk)
        return others.filter(slug=slug).exists()


class VendorTranslation(TranslationModel):
    """A vendor's storefront texts and policies in one language (explicit table, spec 09)."""

    translation_of = "vendor"
    translated_fields = (
        "tagline",
        "description",
        "shipping_policy",
        "refund_policy",
        "cancellation_policy",
    )

    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="translations")
    tagline = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    shipping_policy = models.TextField(blank=True)
    refund_policy = models.TextField(blank=True)
    cancellation_policy = models.TextField(blank=True)

    class Meta(TranslationModel.Meta):
        ordering = ["vendor", "language"]
        constraints = [
            *TranslationModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["vendor", "language"], name="vendors_translation_vendor_language_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.vendor} [{self.language}]"


class VendorMemberRole(models.TextChoices):
    OWNER = engine.OWNER, "Owner"
    STAFF = engine.STAFF, "Staff"


class VendorMember(BaseModel):
    """A user's membership of a vendor, with a role (spec 02 §2.2, CORE.2).

    Created, re-roled and removed only through `services.add_member`, `change_member_role` and
    `remove_member`, which keep at least one owner per vendor and write the audit log. What a
    role may do is the matrix in `engine.ROLE_PERMISSIONS`.
    """

    Role = VendorMemberRole

    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="members")
    # PROTECT: a membership disappears through `remove_member` (last-owner rule, audit), never as
    # a side effect of deleting the user.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="vendor_memberships"
    )
    role = models.CharField(max_length=16, choices=Role.choices, editable=False)

    class Meta:
        ordering = ["vendor", "role", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["vendor", "user"], name="vendors_member_vendor_user_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(role__in=VendorMemberRole.values),
                name="vendors_member_role_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user} @ {self.vendor} ({self.role})"
